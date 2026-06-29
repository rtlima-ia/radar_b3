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
    yield

# Inicia o robô de monitoramento de forma independente e robusta
thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
thread_robo.start()
print("🤖 Robô de monitoramento iniciado com sucesso!")

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
# 3. FUNÇÕES DE ENVIO DE E-MAIL (BREVO API v3)
# ==========================================

def enviar_email_via_resend(destino, assunto, corpo_texto):
    if not BREVO_API_KEY:
        print("⚠️ Erro: BREVO_API_KEY não configurada no ambiente.")
        return

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "sender": {
            "name": "Radar B3",
            "email": EMAIL_REMETENTE
        },
        "to": [{"email": destino}],
        "subject": assunto,
        "textContent": corpo_texto
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201, 202]:
            print(f"📧 E-mail via Brevo enviado com sucesso para {destino}!")
        else:
            print(f"❌ Falha ao enviar e-mail pelo Brevo: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"💥 Erro na conexão com a API do Brevo: {e}")

def enviar_email_confirmacao(destino, ativo, preco_atual, preco_alvo, condicao: int):
    texto_condicao = "MAIOR ou igual a" if condicao == 1 else "MENOR ou igual a"
    corpo = (
        f"✅ MONITORAMENTO CONFIGURADO COM SUCESSO!\n\n"
        f"Seu robô para o ativo {ativo} está ativo.\n\n"
        f"📊 Cotação Atual de Mercado: R$ {preco_atual:.2f}\n"
        f"🎯 Seu Preço Alvo: R$ {preco_alvo:.2f}\n"
        f"⚙️ Regra de Disparo: Avisar quando o preço ficar {texto_condicao} R$ {preco_alvo:.2f}\n\n"
        f"O Radar B3 enviará uma mensagem assim que este objetivo for atingido!"
    )
    enviar_email_via_resend(destino, f"📡 Radar B3: Monitoramento {ativo} Ativado!", corpo)

def enviar_email_b3(destino, ativo, preco_alvo, preco_atual, condicao: int):
    acao_sugerida = "🚨 HORA DE VENDER (Preço Alto)" if condicao == 1 else "🟢 OPORTUNIDADE DE COMPRA (Preço Baixo)"
    corpo = (
        f"🚨 ALVO ATINGIDO!\n\n"
        f"O ativo {ativo} atingiu o objetivo configurado.\n\n"
        f"📌 Situação: {acao_sugerida}\n"
        f"Preço Alvo Configurado: R$ {preco_alvo:.2f}\n"
        f"Preço Atual de Mercado: R$ {preco_atual:.2f}\n\n"
        f"Este monitoramento foi encerrado e removido do radar dinâmico."
    )
    enviar_email_via_resend(destino, f"🔔 Radar B3: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

def enviar_email_token_consulta(destino, codigo):
    corpo = (
        f"🔑 SEU CÓDIGO DE ACESSO — RADAR B3\n\n"
        f"Você solicitou a consulta dos seus monitoramentos ativos.\n\n"
        f"Utilize o código de segurança abaixo no site para carregar a sua lista de robôs em tempo real:\n"
        f"👉 {codigo} 👈\n\n"
        f"Após inserir este código, você poderá selecionar individualmente quais alertas deseja manter ou desativar.\n"
        f"Se você não solicitou este acesso, apenas ignore este e-mail."
    )
    enviar_email_via_resend(destino, "🔒 Código de Acesso - Radar B3", corpo)

def obter_preco_interno(ativo_nome: str) -> float:
    nome_ativo = ativo_nome.strip().upper()
    ticker_yahoo = f"{nome_ativo}.SA" if not nome_ativo.endswith(".SA") else nome_ativo
    nome_ativo = nome_ativo.replace(".SA", "")
    tempo_atual = time.time()

    if nome_ativo in CACHE_COTCOES:
        dados_cache = CACHE_COTCOES[nome_ativo]
        if tempo_atual - dados_cache["timestamp"] < CACHE_EXPIRACAO_SEGUNDOS:
            return float(dados_cache["preco"])

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yahoo}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/122.0.0.0"
        }
        resposta = requests.get(url, headers=headers, timeout=4)
        preco_atual = None
        if resposta.status_code == 200:
            preco_atual = resposta.json().get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
        
        if preco_atual is None:
            ticker_obj = yf.Ticker(ticker_yahoo)
            hist = ticker_obj.history(period="1d")
            if not hist.empty:
                preco_atual = hist["Close"].iloc[-1]
            else:
                preco_atual = ticker_obj.info.get("regularMarketPrice") or ticker_obj.info.get("currentPrice")

        if preco_atual is not None:
            preco_final = round(float(preco_atual), 2)
            CACHE_COTCOES[nome_ativo] = {"preco": preco_final, "timestamp": tempo_atual}
            return preco_final
    except Exception as e:
        print(f"⚠️ Alerta ao buscar cotação de {nome_ativo}: {e}")
        
    if nome_ativo in CACHE_COTCOES:
        return CACHE_COTCOES[nome_ativo]["preco"]
    return 0.0

# ==========================================
# 4. ROTAS DO FASTAPI (INTERFACE E APIS)
# ==========================================

@app.get("/ads.txt", response_class=PlainTextResponse)
def obter_ads_txt():
    return "google.com, pub-9200830725654504, DIRECT, f08c47fec0942fa0"

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    html_content = r"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="google-adsense-account" content="ca-pub-9200830725654504">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Radar B3 - Monitorando Ativos</title>
        <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23003366'/%3E%3Cpolyline points='6,22 12,14 18,20 26,8' fill='none' stroke='%23ec7000' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='26' cy='8' r='3' fill='%23ec7000'/%3E%3C/svg%3E">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gradient-to-b from-[#ff7a00] to-[#b34f00] text-white min-h-screen flex flex-col items-center justify-between font-sans p-4 relative overflow-x-hidden selection:bg-[#ff8c21] selection:text-white">
        
        <div class="absolute top-[-20%] left-1/2 -translate-x-1/2 w-[800px] h-[500px] bg-white/15 rounded-full blur-[120px] pointer-events-none"></div>

        <div class="flex-grow flex items-center justify-center w-full z-10">
            <div class="max-w-xl w-full bg-gradient-to-b from-[#003366] to-[#001c3a] p-8 rounded-2xl shadow-lg border border-white/10 my-8">
                <div class="text-center mb-6">
                    <h1 class="text-3xl font-black tracking-tight text-white">Radar B3</h1>
                    <p class="text-orange-200 mt-2 text-sm font-medium tracking-wide">Ferramenta gratuita de monitoramento de ativos em tempo real</p>
                </div>

                <div class="flex bg-[#001428] rounded-xl p-1 mb-6 border border-white/5">
                    <button id="tabCadastro" class="flex-1 py-2.5 text-sm font-extrabold text-white bg-white/10 rounded-lg focus:outline-none transition-all duration-200">
                        📝 Criar Alerta
                    </button>
                    <button id="tabCancelamento" class="flex-1 py-2.5 text-sm font-extrabold text-orange-200/60 focus:outline-none hover:text-white transition-all duration-200">
                        🔍 Consultar & Cancelar
                    </button>
                </div>

                <form id="formB3" class="space-y-4">
                    <div>
                        <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1.5 opacity-80">Código do Ativo (ex: PETR4, MXRF11)</label>
                        <div class="relative">
                            <input type="text" id="ativo" placeholder="Digite e clique fora..." required
                                class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-white font-bold focus:outline-none focus:border-[#ff8c21] uppercase transition placeholder-slate-600 text-sm">
                            <span id="precoTempoReal" class="absolute right-3 top-3.5 text-xs font-black text-white hidden"></span>
                        </div>
                    </div>

                    <div>
                        <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1.5 opacity-80">Seu E-mail para Alerta</label>
                        <input type="email" id="email" placeholder="seuemail@exemplo.com" required
                            class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-white focus:outline-none focus:border-[#ff8c21] transition placeholder-slate-600 text-sm">
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1.5 opacity-80">Preço Alvo Desejado</label>
                            <input type="text" id="preco" placeholder="R$ 0,00" required
                                class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-white focus:outline-none focus:border-[#ff8c21] transition placeholder-slate-600 text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1.5 opacity-80">Me avise quando for:</label>
                            <select id="condicao" class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-white font-bold focus:outline-none focus:border-[#ff8c21] text-sm">
                                <option value="1" class="bg-[#002244]">📈 Maior ou Igual</option>
                                <option value="0" class="bg-[#002244]">📉 Menor ou Igual</option>
                            </select>
                        </div>
                    </div>

                    <button type="submit" class="w-full bg-gradient-to-b from-[#ff912b] to-[#ec7000] hover:from-[#ff9c3f] hover:to-[#db6800] text-white font-black py-4 px-4 rounded-xl transition duration-150 tracking-wide uppercase text-sm shadow-md">
                        Ativar 🚀
                    </button>
                </form>

                <div id="containerCancelamento" class="space-y-4 pb-4 hidden">
                    <form id="formSolicitarCancelamento" class="space-y-4">
                        <div>
                            <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1 opacity-80">Seu E-mail Cadastrado</label>
                            <input type="email" id="emailCancelamento" placeholder="seuemail@exemplo.com" required
                                class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-white focus:outline-none focus:border-blue-400 text-sm">
                        </div>
                        <button type="submit" class="w-full bg-white/10 hover:bg-white/15 text-white font-bold py-3 px-4 rounded-xl border border-white/10 transition duration-150 text-xs uppercase tracking-wider shadow-sm">
                            Solicitar Código de Consulta 🔑
                        </button>
                    </form>

                    <form id="formAutenticarConsulta" class="space-y-4 hidden border-t border-white/10 pt-4">
                        <div>
                            <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider mb-1 opacity-80">Insira o Código de 6 Dígitos</label>
                            <input type="text" id="codigoSeguranca" placeholder="Ex: 123456" maxlength="6" required
                                class="w-full bg-[#001224] border border-white/5 rounded-xl px-4 py-3.5 text-center text-xl font-bold tracking-widest text-white focus:outline-none focus:border-[#ff8c21]">
                        </div>
                        <button type="submit" class="w-full bg-gradient-to-b from-[#ff912b] to-[#ec7000] text-white font-black py-4 px-4 rounded-xl transition duration-100 shadow-md uppercase text-sm tracking-wide">
                            Buscar Monitoramentos 🔍
                        </button>
                    </form>

                    <div id="wrapperListagemAlertas" class="space-y-4 hidden border-t border-white/10 pt-4">
                        <label class="block text-xs font-bold text-orange-100 uppercase tracking-wider">Selecione o que deseja cancelar:</label>
                        <div id="listaAlertasDinamica" class="space-y-2 max-h-64 overflow-y-auto pr-1" style="scrollbar-width: thin;"></div>
                        <button id="btnConfirmarCancelamentoLote" class="w-full bg-gradient-to-b from-red-500 to-red-600 hover:from-red-600 hover:to-red-700 text-white font-bold py-3.5 px-4 rounded-xl transition duration-150 shadow-md hidden uppercase tracking-wider text-sm">
                            Cancelar 🔒
                        </button>
                    </div>
                </div>

                <div id="feedback" class="mt-6 hidden p-5 rounded-xl border border-white/10 transition-all duration-300 text-sm font-medium"></div>

                <div class="mt-6 pt-4 border-t border-white/5 flex justify-center">
                    <ins class="adsbygoogle" style="display:block; min-width:300px; max-width:100%;" data-ad-client="ca-pub-9200830725654504" data-ad-slot="0000000000" data-ad-format="auto" data-full-width-responsive="true"></ins>
                    <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
                </div>
            </div>
        </div>

        <footer class="w-full text-center py-4 border-t border-white/5 bg-black/15 text-xs text-orange-100/60 backdrop-blur-md z-10">
            <p>&copy; 2026 Radar B3. Todos os direitos reservados. O site não realiza recomendações de investimentos.</p>
            <p class="mt-1"><a href="/politica-de-privacidade" target="_blank" class="hover:text-white underline transition-all">Política de Privacidade</a></p>
        </footer>

        <script>
            const tabCadastro = document.getElementById('tabCadastro');
            const tabCancelamento = document.getElementById('tabCancelamento');
            const formB3 = document.getElementById('formB3');
            const containerCancelamento = document.getElementById('containerCancelamento');
            const formSolicitarCancelamento = document.getElementById('formSolicitarCancelamento');
            const inputAtivo = document.getElementById('ativo');
            const inputPreco = document.getElementById('preco');
            const selectCondicao = document.getElementById('condicao');
            const precoTempoReal = document.getElementById('precoTempoReal');
            const feedback = document.getElementById('feedback');

            let valorCotacaoAtual = 0;
            let precoLimpoParaEnvio = 0;

            tabCadastro.addEventListener('click', () => {
                tabCadastro.className = "flex-1 py-2.5 text-sm font-extrabold text-white bg-white/10 rounded-lg focus:outline-none transition-all duration-200";
                tabCancelamento.className = "flex-1 py-2.5 text-sm font-extrabold text-orange-200/60 focus:outline-none hover:text-white transition-all duration-200";
                formB3.classList.remove('hidden');
                containerCancelamento.classList.add('hidden');
                feedback.classList.add('hidden');
            });

            tabCancelamento.addEventListener('click', () => {
                tabCancelamento.className = "flex-1 py-2.5 text-sm font-extrabold text-white bg-white/10 rounded-lg focus:outline-none transition-all duration-200";
                tabCadastro.className = "flex-1 py-2.5 text-sm font-extrabold text-orange-200/60 focus:outline-none hover:text-white transition-all duration-200";
                formB3.classList.add('hidden');
                containerCancelamento.classList.remove('hidden');
                feedback.classList.add('hidden');
            });

            inputPreco.addEventListener('input', (e) => {
                let value = e.target.value.replace(/\D/g, "");
                if (value === "") { precoLimpoParaEnvio = 0; e.target.value = ""; return; }
                precoLimpoParaEnvio = parseFloat(value) / 100;
                e.target.value = precoLimpoParaEnvio.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                executarSugestaoCondicao();
            });

            function executarSugestaoCondicao() {
                if (valorCotacaoAtual === 0 || precoLimpoParaEnvio === 0) return;
                selectCondicao.value = precoLimpoParaEnvio > valorCotacaoAtual ? "1" : "0";
            }

            inputAtivo.addEventListener('blur', async () => {
                let ativoVal = inputAtivo.value.trim().toUpperCase().replace(".SA", "");
                if (!ativoVal) return;
                
                precoTempoReal.className = "absolute right-3 top-3.5 text-xs font-bold text-orange-200 animate-pulse";
                precoTempoReal.innerText = "Buscando...";
                precoTempoReal.classList.remove('hidden');
                
                try {
                    const endpoint = `/api/preco/${ativoVal}`;
                    const response = await fetch(endpoint);
                    const dados = await response.json();
                    
                    if (dados.status === "sucesso" && dados.preco_atual > 0) {
                        valorCotacaoAtual = dados.preco_atual;
                        precoTempoReal.className = "absolute right-3 top-3.5 text-xs font-black text-white bg-[#ec7000] px-2.5 py-0.5 rounded-lg shadow border-t border-white/10 animate-none";
                        precoTempoReal.innerText = `R$ ${valorCotacaoAtual.toFixed(2)}`;
                        executarSugestaoCondicao();
                    } else {
                        precoTempoReal.className = "absolute right-3 top-3.5 text-xs font-bold text-red-300 bg-red-950/40 px-2 py-0.5 rounded-lg border border-red-500/20";
                        precoTempoReal.innerText = "Ausente";
                        valorCotacaoAtual = 0;
                    }
                } catch (err) {
                    precoTempoReal.className = "absolute right-3 top-3.5 text-xs font-bold text-red-400 bg-red-950/40 px-2 py-0.5 rounded-lg border border-red-500/20";
                    precoTempoReal.innerText = "Inativo";
                    valorCotacaoAtual = 0;
                }
            });

            formB3.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (precoLimpoParaEnvio <= 0) { alert("Digite um preço alvo válido."); return; }
                feedback.className = "mt-6 p-5 rounded-xl border bg-white/5 text-orange-100 border-white/10 text-center text-sm font-medium animate-pulse";
                feedback.innerText = "Registrando monitoramento no sistema...";
                feedback.classList.remove('hidden');

                try {
                    const response = await fetch('/api/alerta', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({
                            'email': document.getElementById('email').value,
                            'ativo': inputAtivo.value,
                            'preco_alvo': precoLimpoParaEnvio,
                            'condicao': selectCondicao.value
                        })
                    });
                    const dados = await response.json();
                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-[#001224] border-white/10 text-left space-y-3";
                        feedback.innerHTML = `
                            <div class="border-b border-white/10 pb-2"><span class="text-base font-bold text-white block">🎉 MONITORAMENTO ATIVADO</span></div>
                            <p class="text-sm text-orange-100">O robô está em operation. Destino: <span class="text-white underline font-bold">${dados.email}</span></p>
                        `;
                        formB3.reset();
                        precoTempoReal.classList.add('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro ao conectar com o servidor.";
                }
            });

            formSolicitarCancelamento.addEventListener('submit', async (e) => {
                e.preventDefault();
                feedback.className = "mt-6 p-5 rounded-xl border bg-white/5 text-orange-100 border-white/10 text-center text-sm font-medium animate-pulse";
                feedback.innerText = "Validando credenciais...";
                feedback.classList.remove('hidden');

                try {
                    const response = await fetch('/api/cancelar/solicitar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 'email': document.getElementById('emailCancelamento').value })
                    });
                    const dados = await response.json();
                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-[#001224] text-white border-white/10 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                        document.getElementById('formAutenticarConsulta').classList.remove('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro de conexão.";
                }
            });

            document.getElementById('formAutenticarConsulta').addEventListener('submit', async (e) => {
                e.preventDefault();
                try {
                    const response = await fetch('/api/cancelar/listar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 
                            'email': document.getElementById('emailCancelamento').value, 
                            'codigo': document.getElementById('codigoSeguranca').value 
                        })
                    });
                    const dados = await response.json();
                    if (dados.status === "sucesso") {
                        feedback.classList.add('hidden');
                        const listaDiv = document.getElementById('listaAlertasDinamica');
                        listaDiv.innerHTML = "";
                        dados.alertas.forEach(alerta => {
                            const precoAtualTexto = alerta.preco_atual > 0 ? `R$ ${alerta.preco_atual.toFixed(2)}` : "Carregando...";
                            const simboloCondicao = Number(alerta.condicao) === 1 ? "📈 ≥" : "📉 ≤";
                            
                            const itemHtml = `
                                <label class="flex items-center justify-between p-3.5 bg-[#001224] rounded-xl border border-white/5 hover:border-white/10 cursor-pointer transition shadow-sm">
                                    <div class="flex items-center gap-3">
                                        <input type="checkbox" value="${alerta.id}" class="w-4 h-4 rounded-md accent-[#ec7000] checkbox-alerta-cancelar">
                                        <div class="flex flex-col">
                                            <span class="font-black text-amber-400 uppercase tracking-wider">${alerta.ativo}</span>
                                            <span class="text-[10px] text-slate-400">Mercado: <b class="text-white">${precoAtualTexto}</b></span>
                                        </div>
                                    </div>
                                    <span class="text-xs font-bold text-orange-200">Alvo: <span class="text-[#ff8c21] font-black">${simboloCondicao} R$ ${alerta.preco_alvo.toFixed(2)}</span></span>
                                </label>
                            `;
                            listaDiv.insertAdjacentHTML('beforeend', itemHtml);
                        });
                        document.getElementById('wrapperListagemAlertas').classList.remove('hidden');
                        document.getElementById('btnConfirmarCancelamentoLote').classList.remove('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro na consulta dos dados.";
                }
            });

            document.getElementById('btnConfirmarCancelamentoLote').addEventListener('click', async () => {
                const checkboxes = document.querySelectorAll('.checkbox-alerta-cancelar:checked');
                const idsParaCancelar = Array.from(checkboxes).map(cb => cb.value);
                if (idsParaCancelar.length === 0) { alert("Selecione ao menos um item."); return; }

                try {
                    const response = await fetch('/api/cancelar/confirmar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({
                            'email': document.getElementById('emailCancelamento').value,
                            'codigo': document.getElementById('codigoSeguranca').value,
                            'ids': idsParaCancelar.join(',')
                        })
                    });
                    const dados = await response.json();
                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-[#001224] text-white border-white/10 text-center text-sm font-bold uppercase tracking-wider";
                        feedback.innerText = `🔒 ${dados.mensagem}`;
                        document.getElementById('formSolicitarCancelamento').reset();
                        document.getElementById('formAutenticarConsulta').reset();
                        document.getElementById('formAutenticarConsulta').classList.add('hidden');
                        document.getElementById('wrapperListagemAlertas').classList.add('hidden');
                    }
                } catch (err) {
                    alert("Erro ao processar remoção.");
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/politica-de-privacidade", response_class=HTMLResponse)
def pagina_politica_privacidade():
    html_politica = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Política de Privacidade - Radar B3</title>
        <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23ec7000'/%3E%3Crect x='4' y='4' width='24' height='24' rx='4' fill='%23003366'/%3E%3Ccircle cx='16' cy='16' r='8' fill='none' stroke='%23ec7000' stroke-width='2'/%3E%3Ccircle cx='16' cy='16' r='2' fill='%23ffffff'/%3E%3C/svg%3E">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#ec7000] text-white font-sans p-6 min-h-screen flex items-center justify-center">
        <div class="max-w-2xl w-full bg-[#003366] p-8 rounded-2xl border border-white/10 shadow-md space-y-4">
            <h1 class="text-2xl font-bold text-white">🔒 Política de Privacidade</h1>
            <p class="text-orange-100 text-sm">O <b>Radar B3</b> respeita integralmente as normas de privacidade dos seus usuários. Processamos os e-mails informados de forma estrita e exclusiva para disparar os monitoramentos configurados de forma autônoma.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_politica)

@app.get("/api/preco/{ativo}")
@app.get("/api/preco")
def obter_preco_ativo(ativo: str = None):
    if not ativo:
        return {"status": "erro", "mensagem": "O código do ativo é obrigatório."}
    preco = obter_preco_interno(ativo)
    if preco > 0:
        return {"status": "sucesso", "ativo": ativo.strip().upper(), "preco_atual": preco}
    return {"status": "erro", "mensagem": "Cotação indisponível."}

@app.post("/api/alerta")
def configuring_alerta(
    email: str = Form(...),
    ativo: str = Form(...),
    preco_alvo: float = Form(...),
    condicao: int = Form(...), 
    db: Session = Depends(get_db)
):
    email_limpo = email.strip().lower()
    if not re.match(EMAIL_REGEX, email_limpo):
        return {"status": "erro", "mensagem": "Por favor, insira um e-mail válido."}

    ticker = ativo.strip().upper().replace(".SA", "")
    
    alerta_duplicado = db.query(Alerta).filter(
        Alerta.email == email_limpo,
        Alerta.ativo == ticker,
        Alerta.preco_alvo == preco_alvo,
        Alerta.condicao == condicao
    ).first()

    if alerta_duplicado:
        return {
            "status": "erro", 
            "mensagem": f"Você já possui um monitoramento ativo exatamente igual para {ticker} nesta mesma condição e preço alvo!"
        }

    preco_atual = obter_preco_interno(ticker)
    if preco_atual == 0.0:
        return {"status": "erro", "mensagem": "Falha ao validar cotação do ativo."}

    novo_alerta = Alerta(email=email_limpo, ativo=ticker, preco_alvo=preco_alvo, condicao=condicao)
    db.add(novo_alerta)
    db.commit()

    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)
    return {"status": "sucesso", "ativo": ticker, "email": novo_alerta.email}

@app.post("/api/cancelar/solicitar")
def solicitar_cancelamento(email: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    alertas_ativos = db.query(Alerta).filter(Alerta.email == email_limpo).all()
    
    if not alertas_ativos:
        return {"status": "erro", "mensagem": "Não encontramos nenhum monitoramento ativo para este e-mail."}
        
    codigo_seguranca = str(random.randint(100000, 999999))
    db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo).delete()
    
    novo_codigo = CodigoCancelamento(email=email_limpo, codigo=codigo_seguranca)
    db.add(novo_codigo)
    db.commit()
    
    enviar_email_token_consulta(email_limpo, codigo_seguranca)
    return {"status": "sucesso", "mensagem": "Código enviado! Verifique sua caixa de entrada."}

@app.post("/api/cancelar/listar")
def listar_monitoramentos_usuario(email: str = Form(...), codigo: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    codigo_limpo = codigo.strip()
    
    reg = db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo, CodigoCancelamento.codigo == codigo_limpo).first()
    if not reg:
        raise HTTPException(status_code=403, detail="Código inválido ou e-mail incorreto.")
        
    alertas = db.query(Alerta).filter(Alerta.email == email_limpo).all()
    
    ativos_usuario = list(set([a.ativo for a in alertas]))
    cotacoes_usuario = {}
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        resultados = executor.map(obter_preco_interno, ativos_usuario)
        for ativo, preco in zip(ativos_usuario, resultados):
            if preco > 0:
                cotacoes_usuario[ativo] = preco

    retorno_alertas = []
    for a in alertas:
        retorno_alertas.append({
            "id": a.id, 
            "ativo": a.ativo, 
            "preco_alvo": a.preco_alvo,
            "preco_atual": cotacoes_usuario.get(a.ativo, 0.0),
            "condicao": a.condicao 
        })
        
    return {"status": "sucesso", "alertas": retorno_alertas}

@app.post("/api/cancelar/confirmar")
def confirmar_cancelamento(email: str = Form(...), codigo: str = Form(...), ids: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    codigo_limpo = codigo.strip()
    
    reg = db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo, CodigoCancelamento.codigo == codigo_limpo).first()
    if not reg:
        return {"status": "erro", "mensagem": "Token de segurança inválido."}
        
    lista_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not lista_ids:
        return {"status": "erro", "mensagem": "Nenhum monitoramento válido selecionado."}
        
    alertas_removidos = db.query(Alerta).filter(
        Alerta.id.in_(lista_ids),
        Alerta.email == email_limpo
    ).delete(synchronize_session=False)
    
    db.delete(reg)
    db.commit()
    
    return {"status": "sucesso", "mensagem": f"Sucesso! {alertas_removidos} monitoramento(s) excluído(s) da base."}

def loop_monitoramento_b3():
    print("🤖 Robô de monitoramento de ativos B3 iniciado com foco em alta performance!")
    while True:
        db = SessionLocal()
        try:
            alertas_ativos = db.query(Alerta).all()
            if alertas_ativos:
                print(f"📊 Verificando {len(alertas_ativos)} monitoramentos no radar...")
                ativos_unicos = list(set([a.ativo for a in alertas_ativos]))
                cotacoes = {}

                with ThreadPoolExecutor(max_workers=12) as executor:
                    resultados = executor.map(obter_preco_interno, ativos_unicos)
                    for ativo, preco in zip(ativos_unicos, resultados):
                        if preco > 0:
                            cotacoes[ativo] = preco

                for alerta in alertas_ativos:
                    preco_atual = cotacoes.get(alerta.ativo)
                    if preco_atual is None:
                        continue

                    disparar = False
                    if alerta.condicao == 1 and preco_atual >= alerta.preco_alvo:
                        disparar = True
                    elif alerta.condicao == 0 and preco_atual <= alerta.preco_alvo:
                        disparar = True

                    if disparar:
                        try:
                            enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                            db.delete(alerta)
                            db.commit()
                        except Exception as inner_e:
                            print(f"⚠️ Falha ao processar disparo individual: {inner_e}")
                            db.rollback()
        except Exception as e:
            print(f"💥 Erro geral no loop do monitor: {e}")
        finally:
            db.close()
        time.sleep(300)