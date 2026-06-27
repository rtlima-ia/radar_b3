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

app = FastAPI(title="Monitora Bolsa - Monitorando Ativos", lifespan=lifespan)

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
# 3. FUNÇÕES DE SUPORTE
# ==========================================

def enviar_email_via_resend(destino, assunto, corpo_texto):
    if not BREVO_API_KEY: return
    requests.post("https://api.brevo.com/v3/smtp/email", json={
        "sender": {"name": "Monitora Bolsa", "email": EMAIL_REMETENTE},
        "to": [{"email": destino}], "subject": assunto, "textContent": corpo_texto
    }, headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, timeout=10)

def obter_preco_interno(ativo_nome: str) -> float:
    nome_ativo = ativo_nome.strip().upper().replace(".SA", "")
    tempo_atual = time.time()
    if nome_ativo in CACHE_COTCOES and (tempo_atual - CACHE_COTCOES[nome_ativo]["timestamp"]) < CACHE_EXPIRACAO_SEGUNDOS:
        return float(CACHE_COTCOES[nome_ativo]["preco"])
    try:
        ticker = yf.Ticker(f"{nome_ativo}.SA")
        preco = ticker.info.get("regularMarketPrice") or ticker.info.get("currentPrice")
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
    <html lang="pt-PT">
    <head>
        <meta charset="UTF-8">
        <meta name="google-adsense-account" content="ca-pub-9200830725654504">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monitora Bolsa - Monitoramento Financeiro</title>
        <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23001D3D'/%3E%3Ccircle cx='16' cy='16' r='8' stroke='%2348CAE4' stroke-width='4' fill='none'/%3E%3C/svg%3E">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gradient-to-br from-[#001D3D] to-[#000814] text-[#E0FBFC] min-h-screen flex flex-col items-center p-4">
        <div class="max-w-xl w-full bg-[#001D3D] p-8 rounded-2xl shadow-2xl border border-[#48CAE4]/20 my-8">
            <div class="text-center mb-6">
                <h1 class="text-3xl font-black text-white">Monitora Bolsa</h1>
                <p class="text-[#90E0EF] mt-2 text-sm font-medium">Ferramenta gratuita de monitoramento de ativos da Bolsa de Valores brasileira. Configure alertas e receba notificações por e-mail automaticamente.</p>
            </div>
            
            <form id="formB3" class="space-y-4">
                <input type="text" id="ativo" placeholder="Ativo (ex: PETR4)" required class="w-full bg-[#000814] border border-[#48CAE4]/30 rounded-xl px-4 py-3 text-white font-bold uppercase text-sm focus:border-[#48CAE4] outline-none">
                <input type="email" id="email" placeholder="Seu E-mail" required class="w-full bg-[#000814] border border-[#48CAE4]/30 rounded-xl px-4 py-3 text-white text-sm focus:border-[#48CAE4] outline-none">
                <div class="grid grid-cols-2 gap-4">
                    <input type="text" id="preco" placeholder="R$ 0,00" required class="w-full bg-[#000814] border border-[#48CAE4]/30 rounded-xl px-4 py-3 text-white text-sm focus:border-[#48CAE4] outline-none">
                    <select id="condicao" class="w-full bg-[#000814] border border-[#48CAE4]/30 rounded-xl px-4 py-3 text-[#E0FBFC] font-bold text-sm outline-none">
                        <option value="1">📈 ≥ Alvo</option>
                        <option value="0">📉 ≤ Alvo</option>
                    </select>
                </div>
                <button type="submit" class="w-full bg-[#48CAE4] hover:bg-[#00B4D8] text-[#000814] font-black py-4 rounded-xl uppercase text-sm transition">Ativar Monitoramento 🚀</button>
            </form>
            
            <div id="feedback" class="mt-4 hidden p-4 rounded-xl bg-[#000814] border border-[#48CAE4]/30 text-sm"></div>

            <div class="mt-6 pt-4 border-t border-[#48CAE4]/10 flex justify-center">
                <ins class="adsbygoogle" style="display:block; min-width:300px; max-width:100%;" data-ad-client="ca-pub-9200830725654504" data-ad-slot="0000000000" data-ad-format="auto" data-full-width-responsive="true"></ins>
                <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
            </div>
        </div>

        <script>
            const formB3 = document.getElementById('formB3');
            const feedback = document.getElementById('feedback');
            
            formB3.addEventListener('submit', async (e) => {
                e.preventDefault();
                const ativo = document.getElementById('ativo').value.toUpperCase();
                const email = document.getElementById('email').value;
                const preco = document.getElementById('preco').value;
                const condicaoText = document.getElementById('condicao').options[document.getElementById('condicao').selectedIndex].text;

                feedback.classList.remove('hidden');
                feedback.innerHTML = "Processando...";

                const formData = new URLSearchParams(new FormData(formB3));
                const response = await fetch('/api/alerta', { method: 'POST', body: formData });
                const result = await response.json();

                if (result.status === "sucesso") {
                    feedback.innerHTML = `<span class='font-bold text-white'>✅ MONITORAMENTO ATIVADO</span><br>
                                          Ativo: ${ativo}<br>Preço Alvo: ${preco}<br>Condição: ${condicaoText}<br>E-mail: ${email}`;
                } else {
                    feedback.innerHTML = `<span class='text-red-400'>Erro: ${result.mensagem}</span>`;
                }
            });
        </script>
    </body>
    </html>
    """

def loop_monitoramento_b3():
    while True:
        db = SessionLocal()
        try:
            for a in db.query(Alerta).all():
                preco = obter_preco_interno(a.ativo)
                if preco > 0 and ((a.condicao == 1 and preco >= a.preco_alvo) or (a.condicao == 0 and preco <= a.preco_alvo)):
                    # Lógica de disparo mantida conforme versão original
                    db.delete(a)
                    db.commit()
        except: pass
        finally: db.close()
        time.sleep(300)