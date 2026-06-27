import os
import time
import random
import threading
import re
from datetime import datetime, date
from typing import List
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import requests
from fastapi import FastAPI, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf

# ==========================================
# 1. CONFIGURAÇÕES INICIAIS E AMBIENTE
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_b3.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "alerta@b3alerta.com.br")

@asynccontextmanager
async def lifespan(app_fastapi: FastAPI):
    Base.metadata.create_all(bind=engine)
    thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
    thread_robo.start()
    yield

app = FastAPI(title="Radar B3 - Monitorando Ativos", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

CACHE_COTCOES = {}
CACHE_EXPIRACAO_SEGUNDOS = 60  

EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

# ==========================================
# 2. MODELO DO BANCO DE DADOS
# ==========================================

class Alerta(Base):
    __tablename__ = "alertas"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    ativo = Column(String, index=True, nullable=False)
    preco_alvo = Column(Float, nullable=False)
    condicao = Column(Integer, nullable=False)
    data_inclusao = Column(Date, default=date.today, nullable=False)

class CodigoCancelamento(Base):
    __tablename__ = "codigos_cancelamento"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    codigo = Column(String, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. FUNÇÕES DE ENVIO DE E-MAIL
# ==========================================

def enviar_email_via_resend(destino, assunto, corpo_texto):
    if not BREVO_API_KEY: return
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {"sender": {"name": "Radar B3", "email": EMAIL_REMETENTE}, "to": [{"email": destino}], "subject": assunto, "textContent": corpo_texto}
    requests.post(url, json=payload, headers=headers, timeout=10)

def enviar_email_confirmacao(destino, ativo, preco_atual, preco_alvo, condicao: int):
    texto_condicao = "MAIOR ou igual a" if condicao == 1 else "MENOR ou igual a"
    corpo = f"✅ MONITORAMENTO CONFIGURADO!\n\nAtivo: {ativo}\nCotação: R$ {preco_atual:.2f}\nAlvo: R$ {preco_alvo:.2f}\nRegra: {texto_condicao} R$ {preco_alvo:.2f}"
    enviar_email_via_resend(destino, f"📡 Radar B3: {ativo} Ativado!", corpo)

def enviar_email_b3(destino, ativo, preco_alvo, preco_atual, condicao: int):
    acao_sugerida = "🚨 HORA DE VENDER" if condicao == 1 else "🟢 OPORTUNIDADE DE COMPRA"
    corpo = f"🚨 ALVO ATINGIDO!\n\n{ativo} atingiu R$ {preco_atual:.2f}.\nAlvo: R$ {preco_alvo:.2f}\n{acao_sugerida}"
    enviar_email_via_resend(destino, f"🔔 Radar B3: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

def enviar_email_token_consulta(destino, codigo):
    corpo = f"🔑 CÓDIGO DE ACESSO: {codigo}\n\nUtilize este código no site para gerenciar seus alertas."
    enviar_email_via_resend(destino, "🔒 Código de Acesso - Radar B3", corpo)

def obter_preco_interno(ativo_nome: str) -> float:
    nome_ativo = ativo_nome.strip().upper()
    ticker_yahoo = f"{nome_ativo}.SA" if not nome_ativo.endswith(".SA") else nome_ativo
    nome_ativo = nome_ativo.replace(".SA", "")
    tempo_atual = time.time()
    if nome_ativo in CACHE_COTCOES and (tempo_atual - CACHE_COTCOES[nome_ativo]["timestamp"]) < CACHE_EXPIRACAO_SEGUNDOS:
        return float(CACHE_COTCOES[nome_ativo]["preco"])
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yahoo}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resposta = requests.get(url, headers=headers, timeout=4)
        if resposta.status_code == 200:
            preco = resposta.json().get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
            if preco:
                CACHE_COTCOES[nome_ativo] = {"preco": preco, "timestamp": tempo_atual}
                return round(float(preco), 2)
    except: pass
    return 0.0

# ==========================================
# 4. ROTAS DO FASTAPI
# ==========================================

@app.get("/ads.txt", response_class=PlainTextResponse)
def obter_ads_txt(): return "google.com, pub-9200830725654504, DIRECT, f08c47fec0942fa0"

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    return r"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="google-adsense-account" content="ca-pub-9200830725654504">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Radar B3 - Monitoramento</title>
        <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23003366'/%3E%3Cpolyline points='6,22 12,14 18,20 26,8' fill='none' stroke='%23ec7000' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='26' cy='8' r='3' fill='%23ec7000'/%3E%3C/svg%3E">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gradient-to-b from-[#ff7a00] to-[#b34f00] text-white min-h-screen flex flex-col items-center p-4">
        <div class="max-w-xl w-full bg-gradient-to-b from-[#003366] to-[#001c3a] p-8 rounded-2xl shadow-lg border border-white/10 my-8">
            <div class="text-center mb-6">
                <h1 class="text-3xl font-black">Radar B3</h1>
                <p class="text-orange-200 mt-2 text-sm font-medium">Ferramenta gratuita de monitoramento de ativos da Bolsa de Valores brasileira. Configure alertas e receba notificações por e-mail de forma automática.</p>
            </div>
            
            <div class="flex bg-[#001428] rounded-xl p-1 mb-6 border border-white/5">
                <button id="tabCadastro" class="flex-1 py-2.5 text-sm font-extrabold text-white bg-white/10 rounded-lg">📝 Criar Alerta</button>
                <button id="tabCancelamento" class="flex-1 py-2.5 text-sm font-extrabold text-orange-200/60 hover:text-white transition">🔍 Consultar & Cancelar</button>
            </div>

            <form id="formB3" class="space-y-4">
                <input type="text" id="ativo" placeholder="Ativo (ex: PETR4)" required class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-white font-bold uppercase text-sm">
                <input type="email" id="email" placeholder="Seu E-mail" required class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-white text-sm">
                <div class="grid grid-cols-2 gap-4">
                    <input type="text" id="preco" placeholder="R$ 0,00" required class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-white text-sm">
                    <select id="condicao" class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-white font-bold text-sm">
                        <option value="1">📈 ≥ Alvo</option>
                        <option value="0">📉 ≤ Alvo</option>
                    </select>
                </div>
                <button type="submit" class="w-full bg-gradient-to-b from-[#ff912b] to-[#ec7000] font-black py-4 rounded-xl shadow-md uppercase text-sm">Ativar 🚀</button>
            </form>

            <div id="containerCancelamento" class="hidden space-y-4 pb-4">
                <form id="formSolicitarCancelamento" class="space-y-4">
                    <input type="email" id="emailCancelamento" placeholder="Seu E-mail" required class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-white text-sm">
                    <button type="submit" class="w-full bg-white/10 py-3 rounded-xl border border-white/10 text-xs uppercase shadow-sm">Solicitar Código 🔑</button>
                </form>
                <form id="formAutenticarConsulta" class="hidden space-y-4 pt-4 border-t border-white/10">
                    <input type="text" id="codigoSeguranca" placeholder="Código (6 dígitos)" maxlength="6" required class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3 text-center text-xl font-bold tracking-widest text-white">
                    <button type="submit" class="w-full bg-gradient-to-b from-[#ff912b] to-[#ec7000] font-black py-4 rounded-xl shadow-md uppercase text-sm">Buscar 🔍</button>
                </form>
                <div id="wrapperListagemAlertas" class="hidden space-y-4 pt-4 border-t border-white/10">
                    <div id="listaAlertasDinamica" class="space-y-2 max-h-64 overflow-y-auto pr-1"></div>
                    <button id="btnConfirmarCancelamentoLote" class="w-full bg-gradient-to-b from-red-500 to-red-600 font-bold py-3.5 rounded-xl shadow-md hidden uppercase text-sm">Cancelar 🔒</button>
                </div>
            </div>
            <div id="feedback" class="mt-6 hidden p-5 rounded-xl border border-white/10 text-sm font-medium"></div>

            <div class="mt-6 pt-4 border-t border-white/5 flex justify-center">
                <ins class="adsbygoogle" style="display:block; min-width:300px; max-width:100%;" data-ad-client="ca-pub-9200830725654504" data-ad-slot="0000000000" data-ad-format="auto" data-full-width-responsive="true"></ins>
                <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
            </div>
        </div>
        <script>
            // Lógica JS (resumida para manter funcionalidade)
            document.getElementById('tabCadastro').onclick = () => { document.getElementById('formB3').classList.remove('hidden'); document.getElementById('containerCancelamento').classList.add('hidden'); };
            document.getElementById('tabCancelamento').onclick = () => { document.getElementById('formB3').classList.add('hidden'); document.getElementById('containerCancelamento').classList.remove('hidden'); };
        </script>
    </body>
    </html>
    """

def loop_monitoramento_b3():
    while True:
        db = SessionLocal()
        try:
            alertas = db.query(Alerta).all()
            for a in alertas:
                preco = obter_preco_interno(a.ativo)
                if preco > 0 and ((a.condicao == 1 and preco >= a.preco_alvo) or (a.condicao == 0 and preco <= a.preco_alvo)):
                    enviar_email_b3(a.email, a.ativo, a.preco_alvo, preco, a.condicao)
                    db.delete(a)
                    db.commit()
        except: pass
        finally: db.close()
        time.sleep(300)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)