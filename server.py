from fastapi import FastAPI, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf
import smtplib
from email.mime.text import MIMEText
import asyncio

# 1. Configuração do Banco de Dados
DATABASE_URL = "sqlite:///./radar_b3.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AlertaB3(Base):
    __tablename__ = "alertas_b3"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    ativo = Column(String)
    preco_alvo = Column(Float)
    condicao = Column(String)  # "maior" ou "menor"
    ativo_monitorando = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# 2. Inicialização do FastAPI
app = FastAPI(title="Radar B3 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# CONFIGURAÇÃO DE E-MAIL
EMAIL_REMETENTE = "rtlima.ia@gmail.com"  # Coloque seu Gmail aqui
SENHA_REMETENTE = "pluntrmxwvmnodqb"  # Coloque sua senha de app de 16 letras aqui

# E-mail de Confirmação de Cadastro (Ajuste 4: Incluindo Cotação Atual)
def enviar_email_confirmacao(destino, ativo, preco_atual, preco_alvo, condicao):
    texto_condicao = "MAIOR ou igual a" if condicao == "maior" else "MENOR ou igual a"
    corpo = (
        f"✅ RADAR CONFIGURADO COM SUCESSO!\n\n"
        f"Seu robô para o ativo {ativo} está ativo.\n\n"
        f"📊 Cotação Atual de Mercado: R$ {preco_atual:.2f}\n"
        f"🎯 Seu Preço Alvo: R$ {preco_alvo:.2f}\n"
        f"⚙️ Regra de Disparo: Avisar quando o preço ficar {texto_condicao} R$ {preco_alvo:.2f}\n\n"
        f"O Radar B3 enviará uma mensagem assim que este objetivo for atingido!"
    )
    msg = MIMEText(corpo)
    msg['Subject'] = f"📡 Radar B3: Monitoramento de {ativo} Ativado!"
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = destino

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 587) as server:
            server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
            server.send_message(msg)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")

# E-mail de Objetivo Atingido
def enviar_email_b3(destino, ativo, preco_alvo, preco_atual, condicao):
    acao_sugerida = "🚨 HORA DE VENDER (Preço Alto)" if condicao == "maior" else "🟢 OPORTUNIDADE DE COMPRA (Preço Baixo)"
    corpo = (
        f"🚨 RADAR B3: SEU ALVO FOI ATINGIDO!\n\n"
        f"O ativo {ativo} atingiu o objetivo configurado.\n\n"
        f"📌 Situação: {acao_sugerida}\n"
        f"Preço Alvo Configurado: R$ {preco_alvo:.2f}\n"
        f"Preço Atual de Mercado: R$ {preco_atual:.2f}\n\n"
        f"Este monitoramento foi encerrado."
    )
    msg = MIMEText(corpo)
    msg['Subject'] = f"🔔 Radar B3: {ativo} atingiu R$ {preco_atual:.2f}!"
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = destino

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 587) as server:
            server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
            server.send_message(msg)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")

# 3. O Robô Supervisor
async def monitor_mercado_b3():
    while True:
        db = SessionLocal()
        alertas = db.query(AlertaB3).filter(AlertaB3.ativo_monitorando == True).all()
        ativos_unicos = set([a.ativo for a in alertas])
        
        cotacoes_atuais = {}
        for ativo in ativos_unicos:
            try:
                ticker = yf.Ticker(f"{ativo}.SA")
                preco = ticker.fast_info['last_price']
                if preco:
                    cotacoes_atuais[ativo] = round(preco, 2)
            except Exception:
                continue

        for alerta in alertas:
            preco_atual = cotacoes_atuais.get(alerta.ativo)
            if preco_atual:
                disparou = False
                if alerta.condicao == "maior" and preco_atual >= alerta.preco_alvo:
                    disparou = True
                elif alerta.condicao == "menor" and preco_atual <= alerta.preco_alvo:
                    disparou = True
                
                if disparou:
                    enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                    alerta.ativo_monitorando = False
                    db.commit()
                    
        db.close()
        await asyncio.sleep(30)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_mercado_b3())

# Busca o preço em tempo real
@app.get("/api/preco/{ativo}")
def buscar_preco_atual(ativo: str):
    ticker_b3 = ativo.upper().strip()
    try:
        t = yf.Ticker(f"{ticker_b3}.SA")
        preco_atual = t.fast_info['last_price']
        if not preco_atual:
            raise Exception()
        return {"status": "sucesso", "ativo": ticker_b3, "preco_atual": round(preco_atual, 2)}
    except Exception:
        return {"status": "erro", "mensagem": "Ativo não encontrado."}

# Rota de Cadastro
@app.post("/api/alerta/")
def criar_alerta(
    email: str = Form(...), 
    ativo: str = Form(...), 
    preco_alvo: float = Form(...),
    condicao: str = Form(...),
    db: Session = Depends(get_db)
):
    ticker_b3 = ativo.upper().strip()
    
    # Busca o preço atual apenas para anexar no e-mail de confirmação
    try:
        t = yf.Ticker(f"{ticker_b3}.SA")
        preco_atual = round(t.fast_info['last_price'], 2)
    except Exception:
        preco_atual = 0.0

    novo_alerta = AlertaB3(
        email=email, 
        ativo=ticker_b3, 
        preco_alvo=preco_alvo, 
        condicao=condicao
    )
    db.add(novo_alerta)
    db.commit()
    
    # Envia e-mail contendo a cotação atual
    enviar_email_confirmacao(email, ticker_b3, preco_atual, preco_alvo, condicao)
    
    return {
        "status": "sucesso", 
        "ativo": ticker_b3,
        "preco_atual": preco_atual,
        "preco_alvo": preco_alvo,
        "condicao": condicao,
        "email": email
    }
