import os
import time
import threading
from datetime import datetime
import requests
from fastapi import FastAPI, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf

# ==========================================
# 1. CONFIGURAÇÕES INICIAIS E AMBIENTE
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_b3.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "alertab3@avisapramim.com.br")

app = FastAPI(title="Avisa Pra Mim - Radar B3")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ==========================================
# 2. MODELO DO BANCO DE DADOS
# ==========================================

class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    ativo = Column(String, index=True, nullable=False)
    preco_alvo = Column(Float, nullable=False)
    condicao = Column(String, nullable=False)
    ativo_sistema = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. FUNÇÕES DE ENVIO DE E-MAIL (RESEND API)
# ==========================================

def enviar_email_via_resend(destino, assunto, corpo_texto):
    if not RESEND_API_KEY:
        print("⚠️ Erro: RESEND_API_KEY não configurada no ambiente.")
        return

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": f"Avisa Pra Mim <{EMAIL_REMETENTE}>",
        "to": [destino],
        "subject": assunto,
        "text": corpo_texto
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            print(f"📧 E-mail enviado com sucesso via Resend para {destino}!")
        else:
            print(f"❌ Falha ao enviar e-mail pelo Resend: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"💥 Erro na conexão com a API do Resend: {e}")

def enviar_email_confirmacao(destino, ativo, preco_atual, preco_alvo, condicao):
    texto_condicao = "MAIOR ou igual a" if condicao == "maior" else "MENOR ou igual a"
    corpo = (
        f"✅ MONITORAMENTO CONFIGURADO COM SUCESSO!\n\n"
        f"Seu robô para o ativo {ativo} está ativo.\n\n"
        f"📊 Cotação Atual de Mercado: R$ {preco_atual:.2f}\n"
        f"🎯 Seu Preço Alvo: R$ {preco_alvo:.2f}\n"
        f"⚙️ Regra de Disparo: Avisar quando o preço ficar {texto_condicao} R$ {preco_alvo:.2f}\n\n"
        f"O Avisa Pra Mim enviará uma mensagem assim que este objetivo for atingido!"
    )
    enviar_email_via_resend(destino, f"📡 Avisa Pra Mim: Monitoramento de {ativo} Ativado!", corpo)

def enviar_email_b3(destino, ativo, preco_alvo, preco_atual, condicao):
    acao_sugerida = "🚨 HORA DE VENDER (Preço Alto)" if condicao == "maior" else "🟢 OPORTUNIDADE DE COMPRA (Preço Baixo)"
    corpo = (
        f"🚨 ALVO ATINGIDO!\n\n"
        f"O ativo {ativo} atingiu o objetivo configurado.\n\n"
        f"📌 Situação: {acao_sugerida}\n"
        f"Preço Alvo Configurado: R$ {preco_alvo:.2f}\n"
        f"Preço Atual de Mercado: R$ {preco_atual:.2f}\n\n"
        f"Este monitoramento foi encerrado e removido do radar dinâmico."
    )
    enviar_email_via_resend(destino, f"🔔 Avisa Pra Mim: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

# ==========================================
# 4. ROTAS DO FASTAPI (INTERFACE INTERNA)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    # 💡 SEU DESIGN COMPLETO: Cole todo o conteúdo do seu index.html antigo aqui dentro das três aspas!
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Avisa Pra Mim - Radar B3</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; color: #333; max-width: 500px; margin: 60px auto; padding: 30px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border-radius: 8px; background: #ffffff; }
            h2 { color: #007bff; text-align: center; margin-bottom: 25px; font-weight: 60px; }
            .form-group { margin-bottom: 20px; }
            label { display: block; margin-bottom: 8px; font-weight: 600; font-size: 14px; color: #555; }
            input, select { width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; font-size: 15px; transition: border-color 0.2s; }
            input:focus, select:focus { border-color: #007bff; outline: none; }
            button { background-color: #007bff; color: white; border: none; padding: 14px; border-radius: 6px; cursor: pointer; width: 100%; font-size: 16px; font-weight: bold; margin-top: 10px; transition: background-color 0.2s; }
            button:hover { background-color: #0056b3; }
        </style>
    </head>
    <body>
        <h2>📡 Avisa Pra Mim — Radar B3</h2>
        
        <form action="/configurar-alerta" method="post">
            <div class="form-group">
                <label for="email">Seu E-mail:</label>
                <input type="email" id="email" name="email" placeholder="exemplo@email.com" required>
            </div>
            
            <div class="form-group">
                <label for="ativo">Código da Ação ou FII:</label>
                <input type="text" id="ativo" name="ativo" placeholder="Ex: PETR4, VALE3, HGLG11" required>
            </div>
            
            <div class="form-group">
                <label for="preco_alvo">Preço Alvo (R$):</label>
                <input type="number" step="0.01" id="preco_alvo" name="preco_alvo" placeholder="0.00" required>
            </div>
            
            <div class="form-group">
                <label for="condicao">Me avise quando o preço for:</label>
                <select id="condicao" name="condicao">
                    <option value="maior">Maior ou igual que o Alvo (Venda)</option>
                    <option value="menor">Menor ou igual que o Alvo (Compra)</option>
                </select>
            </div>
            
            <button type="submit">Ativar Monitoramento</button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/configurar-alerta")
def configurar_alerta(
    email: str = Form(...),
    ativo: str = Form(...),
    preco_alvo: float = Form(...),
    condicao: str = Form(...),
    db: Session = Depends(get_db)
):
    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker
        ticker = ticker.replace(".SA", "")

    try:
        dados_acao = yf.Ticker(ticker_yahoo)
        preco_atual = dados_acao.history(period="1d")["Close"].iloc[-1]
    except Exception:
        raise HTTPException(status_code=400, detail=f"Não foi possível encontrar a cotação para o ativo {ticker}.")

    novo_alerta = Alerta(
        email=email.strip().lower(),
        ativo=ticker,
        preco_alvo=preco_alvo,
        condicao=condicao,
        ativo_sistema=True
    )
    db.add(novo_alerta)
    db.commit()

    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)

    return RedirectResponse(url="/", status_code=303)

# ==========================================
# 5. LOOP DE MONITORAMENTO EM SEGUNDO PLANO
# ==========================================

def loop_monitoramento_b3():
    print("🤖 Robô de monitoramento de ativos B3 iniciado com sucesso!")
    while True:
        db = SessionLocal()
        try:
            alertas_ativos = db.query(Alerta).filter(Alerta.ativo_sistema == True).all()

            if alertas_ativos:
                print(f"📊 [{datetime.now().strftime('%H:%M:%S')}] Verificando {len(alertas_ativos)} monitoramentos no radar...")
                
                ativos_unicos = list(set([a.ativo for a in alertas_ativos]))
                cotacoes = {}

                for ativo in ativos_unicos:
                    try:
                        ticker_sa = f"{ativo}.SA"
                        dados = yf.Ticker(ticker_sa)
                        preco_atual = dados.history(period="1d")["Close"].iloc[-1]
                        cotacoes[ativo] = preco_atual
                    except Exception as e:
                        print(f"⚠️ Erro ao buscar cotação de {ativo}: {e}")

                for alerta in alertas_ativos:
                    preco_atual = cotacoes.get(alerta.ativo)
                    if preco_atual is None:
                        continue

                    disparar = False
                    if alerta.condicao == "maior" and preco_atual >= alerta.preco_alvo:
                        disparar = True
                    elif alerta.condicao == "menor" and preco_atual <= alerta.preco_alvo:
                        disparar = True

                    if disparar:
                        print(f"🚨 ALVO ATINGIDO: {alerta.ativo} chegou a R$ {preco_atual:.2f}")
                        enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                        
                        alerta.ativo_sistema = False
                        db.commit()

        except Exception as e:
            print(f"💥 Erro crítico no loop do monitor: {e}")
        finally:
            db.close()

        time.sleep(300)

thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
thread_robo.start()