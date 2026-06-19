from fastapi import FastAPI, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf
import requests
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
    condicao = Column(String)
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

# CONFIGURAÇÃO DO BREVO (Nova forma estável para nuvem)
BREVO_API_KEY = "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" # Cole aqui a chave gigante que você gerou no Brevo
EMAIL_REMETENTE = "rtlima.ia@gmail.com" # Seu e-mail cadastrado lá

def enviar_email_via_web(destino, assunto, corpo_texto):
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"name": "Radar B3", "email": EMAIL_REMETENTE},
        "to": [{"email": destino}],
        "subject": assunto,
        "textContent": corpo_texto
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201, 202]:
            print(f"E-mail enviado com sucesso via Web para {destino}!")
        else:
            print(f"Erro no Brevo: {response.text}")
    except Exception as e:
        print(f"Erro ao conectar com a API de e-mail: {e}")

# E-mail de Confirmação de Cadastro
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
    enviar_email_via_web(destino, f"📡 Radar B3: Monitoramento de {ativo} Ativado!", corpo)

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
    enviar_email_via_web(destino, f"🔔 Radar B3: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

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
    try:
        t = yf.Ticker(f"{ticker_b3}.SA")
        preco_atual = round(t.fast_info['last_price'], 2)
    except Exception:
        preco_atual = 0.0

    novo_alerta = AlertaB3(email=email, ativo=ticker_b3, preco_alvo=preco_alvo, condicao=condicao)
    db.add(novo_alerta)
    db.commit()
    
    enviar_email_confirmacao(email, ticker_b3, preco_atual, preco_alvo, condicao)
    
    return {
        "status": "sucesso", 
        "ativo": ticker_b3,
        "preco_atual": preco_atual,
        "preco_alvo": preco_alvo,
        "condicao": condicao,
        "email": email
    }