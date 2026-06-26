import os
import re
import threading
import time
import datetime
import logging
import requests
from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Float, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf

# ==========================================
# 1. CONFIGURAÇÕES, LOGS E AMBIENTE
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "no-reply@b3alerta.com.br")

if not DATABASE_URL:
    raise ValueError("A variável de ambiente DATABASE_URL não foi definida!")
if not BREVO_API_KEY:
    logger.warning("A variável BREVO_API_KEY não foi definida. E-mails não serão enviados.")

# Ajuste clássico para compatibilidade do PostgreSQL/Neon no SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==========================================
# 2. BANCO DE DADOS (SQLAlchemy)
# ==========================================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    ativo = Column(String, index=True, nullable=False)
    preco_alvo = Column(Float, nullable=False)
    # NOVA REGRA: 0 = menor, 1 = maior
    condicao = Column(Integer, nullable=False)
    # NOVA COLUNA: Data de inclusão automática
    data_inclusao = Column(Date, default=datetime.date.today, nullable=False)

# Cria as tabelas caso não existam (Garante a estrutura no Neon)
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. FUNÇÕES AUXILIARES (Cotação e E-mail)
# ==========================================
def obter_cotacao(ticker: str) -> float:
    """Busca a cotação atual do ativo via yfinance (adiciona .SA se necessário)"""
    ticker_formatado = ticker.upper().strip()
    if not ticker_formatado.endswith(".SA"):
        ticker_formatado = f"{ticker_formatado}.SA"
    
    try:
        ativo_yf = yf.Ticker(ticker_formatado)
        # Tenta pegar o preço atual de mercado ou o último fechamento
        info = ativo_yf.fast_info
        preco = info.get("last_price") or info.get("regular_market_previous_close")
        if preco is not None:
            return round(float(preco), 2)
    except Exception as e:
        logger.error(f"Erro ao buscar cotação para {ticker_formatado}: {e}")
    return None

def enviar_email_brevo(to_email: str, subject: str, html_content: str):
    """Dispara notificações utilizando a API v3 da Brevo"""
    if not BREVO_API_KEY:
        logger.error("Envio de e-mail cancelado: BREVO_API_KEY ausente.")
        return False
        
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"email": SENDER_EMAIL, "name": "B3 Alerta"},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201, 202]:
            logger.info(f"E-mail enviado com sucesso para {to_email}")
            return True
        else:
            logger.error(f"Erro Brevo ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"Falha na conexão com a API da Brevo: {e}")
    return False

# ==========================================
# 4. ROBÔ DE MONITORAMENTO (Background Thread)
# ==========================================
def loop_monitoramento():
    """Varre o banco periodicamente processando as novas regras de condição numérica"""
    logger.info("Robô de Monitoramento inicializado com sucesso!")
    while True:
        db = SessionLocal()
        try:
            alertas = db.query(Alerta).all()
            # Agrupa por ativo para evitar chamadas duplicadas ao yfinance no mesmo loop
            ativos_unicos = {a.ativo for a in alertas}
            cotacoes = {ativo: obter_cotacao(ativo) for ativo in ativos_unicos}
            
            for alerta in alertas:
                preco_atual = cotacoes.get(alerta.ativo)
                if preco_atual is None:
                    continue
                
                disparar = False
                # NOVA REGRA: 0 = menor ou igual, 1 = maior ou igual
                if alerta.condicao == 0 and preco_atual <= alerta.preco_alvo:
                    disparar = True
                elif alerta.condicao == 1 and preco_atual >= alerta.preco_alvo:
                    disparar = True
                
                if disparar:
                    texto_condicao = "abaixo ou igual a" if alerta.condicao == 0 else "acima ou igual a"
                    subject = f"🚨 ALERTA DE PREÇO: {alerta.ativo} atingiu o alvo!"
                    html = f"""
                    <h2>Seu alerta para {alerta.ativo} disparou!</h2>
                    <p>O ativo atingiu o preço de mercado de <b>R$ {preco_atual:.2f}</b>.</p>
                    <p>Sua meta configurada em {alerta.data_inclusao.strftime('%d/%m/%Y')} era ficar {texto_condicao} <b>R$ {alerta.preco_alvo:.2f}</b>.</p>
                    <br>
                    <p><i>Este alerta foi processado e removido do sistema.</i></p>
                    """
                    if enviar_email_brevo(alerta.email, subject, html):
                        # Regra congelada: Alertas disparados são deletados fisicamente do banco
                        db.delete(alerta)
                        db.commit()
                        logger.info(f"Alerta ID {alerta.id} disparado e excluído do banco.")
                        
        except Exception as e:
            logger.error(f"Erro no loop de monitoramento: {e}")
            db.rollback()
        finally:
            db.close()
        
        # Intervalo entre as varreduras de mercado (ex: 60 segundos)
        time.sleep(60)

# Inicializa o robô em uma thread separada que nunca dorme
threading.Thread(target=loop_monitoramento, daemon=True).start()

# ==========================================
# 5. APLICATIVO FASTAPI E ROTAS
# ==========================================
app = FastAPI(title="B3 Alerta")

# Configuração de templates HTML caso utilize renderização direta no backend
templates = Jinja2Templates(directory="templates") if os.path.exists("templates") else None

@app.get("/health")
def health_check():
    """Endpoint de Health Check para o Northflank manter o container vivo"""
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if templates:
        return templates.TemplateResponse("index.html", {"request": request})
    return "<h1>B3 Alerta Online</h1><p>API funcionando perfeitamente.</p>"

@app.post("/alertas/criar")
def criar_alerta(
    ativo: str = Form(...),
    preco_alvo: float = Form(...),
    condicao_texto: str = Form(...), # Recebe 'menor' ou 'maior' do formulário
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    # Trata e limpa a entrada do ticker
    ativo_limpo = ativo.upper().strip().replace(".SA", "")
    
    # Converte o texto recebido para a nova regra numérica do banco de dados
    condicao_num = 1 if condicao_texto.lower() == "maior" else 0
    
    novo_alerta = Alerta(
        ativo=ativo_limpo,
        preco_alvo=preco_alvo,
        condicao=condicao_num,
        email=email.strip().lower()
        # data_inclusao será definida automaticamente via default=datetime.date.today
    )
    
    try:
        db.add(novo_alerta)
        db.commit()
        db.refresh(novo_alerta)
        logger.info(f"Novo alerta registrado com sucesso: {ativo_limpo} para {email}")
        return {"status": "success", "message": "Alerta cadastrado com sucesso!", "id": novo_alerta.id}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar alerta no banco: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao salvar o alerta.")

@app.post("/alertas/listar")
def listar_alertas(email: str = Form(...), db: Session = Depends(get_db)):
    """Retorna a lista de monitoramentos ativos de um e-mail específico"""
    email_limpo = email.strip().lower()
    alertas = db.query(Alerta).filter(Alerta.email == email_limpo).all()
    
    resultado = []
    for a in alertas:
        resultado.append({
            "id": a.id,
            "ativo": a.ativo,
            "preco_alvo": a.preco_alvo,
            "condicao": "maior" if a.condicao == 1 else "menor",
            "data_inclusao": a.data_inclusao.strftime("%d/%m/%Y")
        })
    return resultado

@app.post("/alertas/deletar/{alerta_id}")
def deletar_alerta(alerta_id: int, email: str = Form(...), db: Session = Depends(get_db)):
    """Remove manualmente um alerta antes dele disparar"""
    email_limpo = email.strip().lower()
    alerta = db.query(Alerta).filter(Alerta.id == alerta_id, Alerta.email == email_limpo).first()
    
    if not alerta:
        raise HTTPException(status_code=404, detail="Alerta não encontrado ou não pertence a este e-mail.")
        
    try:
        db.delete(alerta)
        db.commit()
        return {"status": "success", "message": "Alerta removido com sucesso."}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao deletar alerta: {e}")
        raise HTTPException(status_code=500, detail="Erro ao deletar o alerta.")