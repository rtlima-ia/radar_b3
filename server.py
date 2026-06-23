import os
import time
import random
import threading
from datetime import datetime
import requests
from fastapi import FastAPI, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
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

app = FastAPI(title="Alerta B3 - Radar B3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_COTCOES = {}
CACHE_EXPIRACAO_SEGUNDOS = 60  

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

class CodigoCancelamento(Base):
    __tablename__ = "codigos_cancelamento"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    codigo = Column(String, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)

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
        "from": f"Alerta B3 <{EMAIL_REMETENTE}>",
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
        f"O Alerta B3 enviará uma mensagem assim que este objetivo for atingido!"
    )
    enviar_email_via_resend(destino, f"📡 Monitoramento {ativo} Ativado!", corpo)

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
    enviar_email_via_resend(destino, f"🔔 Alerta B3: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

def enviar_email_solicitacao_cancelamento(destino, alertas, codigo):
    texto_alertas = ""
    for a in alertas:
        regra = "Maior ou igual" if a.condicao == "maior" else "Menor ou igual"
        texto_alertas += f"- Ativo: {a.ativo} | Alvo: R$ {a.preco_alvo:.2f} | Regra: {regra}\n"
        
    corpo = (
        f"🔒 SOLICITAÇÃO DE CANCELAMENTO DE MONITORAMENTO\n\n"
        f"Identificamos que você solicitou o encerramento dos seus alertas ativos.\n\n"
        f"📋 Seus monitoramentos atuais no sistema:\n"
        f"{texto_alertas}\n"
        f"🔑 Seu código de segurança para confirmar o cancelamento é:\n"
        f"👉 {codigo} 👈\n\n"
        f"Insira este número de 6 dígitos na tela do site para desativar todos os robôs acima.\n"
        f"Se você não solicitou este código, apenas ignore este e-mail."
    )
    enviar_email_via_resend(destino, "🔒 Código de Segurança - Alerta B3", corpo)

# ==========================================
# 4. ROTAS DO FASTAPI (INTERFACE E APIS)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Radar B3 - Inteligente</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center justify-center font-sans p-4">

        <div class="max-w-xl w-full bg-slate-900 p-8 rounded-2xl shadow-2xl border border-slate-800">
            <div class="text-center mb-6">
                <h1 class="text-3xl font-extrabold text-green-400">📡 Radar B3</h1>
                <p class="text-slate-400 mt-2 text-sm">Automação inteligente e sugestão de operação em tempo real.</p>
            </div>

            <div class="flex border-b border-slate-800 mb-6">
                <button id="tabCadastro" class="flex-1 pb-3 text-sm font-bold text-green-400 border-b-2 border-green-400 focus:outline-none">
                    📝 Criar Alerta
                </button>
                <button id="tabCancelamento" class="flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300">
                    🔒 Cancelar Monitoramento
                </button>
            </div>

            <form id="formB3" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Código do Ativo (ex: PETR4, MXRF11)</label>
                    <div class="relative">
                        <input type="text" id="ativo" placeholder="Digite e clique fora..." required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500 uppercase">
                        <span id="precoTempoReal" class="absolute right-3 top-3 text-xs font-bold text-slate-500 hidden"></span>
                    </div>
                </div>

                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Seu E-mail para Alerta</label>
                    <input type="email" id="email" placeholder="seuemail@exemplo.com" required
                        class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
                </div>

                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Preço Alvo Desejado</label>
                        <input type="text" id="preco" placeholder="R$ 0,00" required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Me avise quando for:</label>
                        <select id="condicao" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
                            <option value="maior">📈 Maior ou Igual</option>
                            <option value="menor">📉 Menor ou Igual</option>
                        </select>
                    </div>
                </div>

                <button type="submit" class="w-full bg-green-500 hover:bg-green-600 text-slate-950 font-bold py-3 px-4 rounded-lg transition duration-200 shadow-lg">
                    Ativar Radar B3 🚀
                </button>
            </form>

            <div id="containerCancelamento" class="space-y-4 hidden">
                <form id="formSolicitarCancelamento" class="space-y-4">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Digite seu E-mail Cadastrado</label>
                        <input type="email" id="emailCancelamento" placeholder="seuemail@exemplo.com" required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-red-500">
                    </div>
                    <button type="submit" class="w-full bg-red-500/20 hover:bg-red-500/30 text-red-400 font-bold py-3 px-4 rounded-lg border border-red-500/30 transition duration-200">
                        Solicitar Código de Segurança 🔑
                    </button>
                </form>

                <form id="formConfirmarCancelamento" class="space-y-4 hidden border-t border-slate-800 pt-4">
                    <div class="p-3 bg-blue-950/30 border border-blue-900/50 rounded-lg text-xs text-blue-300">
                        📧 Enviamos a lista de monitoramentos e o código de 6 dígitos para o seu e-mail.
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Código de Segurança recebido</label>
                        <input type="text" id="codigoSeguranca" placeholder="Ex: 123456" maxlength="6" required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-center text-xl font-bold tracking-widest text-white focus:outline-none focus:border-green-500">
                    </div>
                    <button type="submit" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 px-4 rounded-lg transition duration-200 shadow-lg">
                        Confirmar e Cancelar Monitoramentos 🔒
                    </button>
                </form>
            </div>

            <div id="feedback" class="mt-6 hidden p-5 rounded-xl border"></div>
        </div>

        <script>
            const tabCadastro = document.getElementById('tabCadastro');
            const tabCancelamento = document.getElementById('tabCancelamento');
            const formB3 = document.getElementById('formB3');
            const containerCancelamento = document.getElementById('containerCancelamento');

            const inputAtivo = document.getElementById('ativo');
            const inputPreco = document.getElementById('preco');
            const selectCondicao = document.getElementById('condicao');
            const precoTempoReal = document.getElementById('precoTempoReal');
            const feedback = document.getElementById('feedback');

            let valorCotacaoAtual = 0;
            let precoLimpoParaEnvio = 0;

            // GERENCIAMENTO DAS ABAS (TABS)
            tabCadastro.addEventListener('click', () => {
                tabCadastro.className = "flex-1 pb-3 text-sm font-bold text-green-400 border-b-2 border-green-400 focus:outline-none";
                tabCancelamento.className = "flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300";
                formB3.classList.remove('hidden');
                containerCancelamento.classList.add('hidden');
                feedback.classList.add('hidden');
            });

            tabCancelamento.addEventListener('click', () => {
                tabCancelamento.className = "flex-1 pb-3 text-sm font-bold text-red-400 border-b-2 border-red-400 focus:outline-none";
                tabCadastro.className = "flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300";
                formB3.classList.add('hidden');
                containerCancelamento.classList.remove('hidden');
                feedback.classList.add('hidden');
            });

            // MÁSCARA EM TEMPO REAL
            inputPreco.addEventListener('input', (e) => {
                let value = e.target.value.replace(/\D/g, "");
                if (value === "") {
                    precoLimpoParaEnvio = 0;
                    e.target.value = "";
                    return;
                }
                precoLimpoParaEnvio = parseFloat(value) / 100;
                e.target.value = precoLimpoParaEnvio.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                executarSugestaoCondicao();
            });

            function ejecutarSugestaoCondicao() {
                if (valorCotacaoAtual === 0 || precoLimpoParaEnvio === 0) return;
                if (precoLimpoParaEnvio > valorCotacaoAtual) {
                    selectCondicao.value = "maior";
                } else {
                    selectCondicao.value = "menor";
                }
            }

            inputAtivo.addEventListener('blur', async () => {
                const ativoVal = inputAtivo.value.trim();
                if (!ativoVal) return;

                precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-blue-400 animate-pulse";
                precoTempoReal.innerText = "Buscando...";
                precoTempoReal.classList.remove('hidden');

                try {
                    const response = await fetch(`/api/preco/${ativoVal}`);
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        valorCotacaoAtual = dados.preco_atual;
                        precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-green-400";
                        precoTempoReal.innerText = `Cotação Atual: R$ ${valorCotacaoAtual.toFixed(2)}`;
                        executarSugestaoCondicao();
                    } else {
                        precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-red-500";
                        precoTempoReal.innerText = "Não encontrado";
                        valorCotacaoAtual = 0;
                    }
                } catch (err) {
                    precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-red-500";
                    precoTempoReal.innerText = "Erro de conexão";
                    valorCotacaoAtual = 0;
                }
            });

            // SUBMIT: CADASTRO
            formB3.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (precoLimpoParaEnvio <= 0) {
                    alert("Por favor, digite um preço alvo válido.");
                    return;
                }

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Registrando o seu alerta de monitoramento...";
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
                        const textoRegra = dados.condicao === "maior" ? "📈 MAIOR OU IGUAL" : "📉 MENOR OU IGUAL";
                        const corRegra = dados.condicao === "maior" ? "bg-red-500/20 text-red-400 border-red-500/30" : "bg-green-500/20 text-green-400 border-green-500/30";

                        feedback.className = "mt-6 p-5 rounded-xl border bg-slate-950 border-slate-800 text-left space-y-3 shadow-inner border-green-900/50";
                        feedback.innerHTML = `
                            <div class="border-b border-slate-800 pb-2">
                                <span class="text-base font-bold text-green-400 block">🎉 PRÉ-CADASTRO REALIZADO COM SUCESSO!</span>
                                <span class="text-xs text-slate-400">O robô já iniciou o monitoramento de mercado.</span>
                            </div>
                            <div class="space-y-1 text-sm mt-2">
                                <p class="text-white">• <b>Ativo cadastrado:</b> ${dados.ativo}</p>
                                <p class="text-white">• <b>Cotação de referência:</b> R$ ${dados.preco_atual.toFixed(2)}</p>
                                <p class="text-white">• <b>Seu Preço Alvo:</b> R$ ${dados.preco_alvo.toFixed(2)}</p>
                                <p class="text-white">• <b>Condição de disparo:</b> <span class="text-xs px-2 py-0.5 rounded font-bold ${corRegra}">${textoRegra}</span></p>
                            </div>
                            <div class="pt-3 border-t border-slate-800 text-xs text-emerald-400 font-medium flex items-center gap-1">
                                📧 Um e-mail de confirmação foi enviado para: <span class="text-white underline">${dados.email}</span>
                            </div>
                        `;
                        formB3.reset();
                        precoTempoReal.classList.add('hidden');
                        valorCotacaoAtual = 0;
                        precoLimpoParaEnvio = 0;
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro ao conectar com o servidor.";
                }
            });

            // SUBMIT: SOLICITAR CANCELAMENTO
            document.getElementById('formSolicitarCancelamento').addEventListener('submit', async (e) => {
                e.preventDefault();
                const emailVal = document.getElementById('emailCancelamento').value;

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Buscando cadastros e enviando código...";
                feedback.classList.remove('hidden');

                try {
                    const response = await fetch('/api/cancelar/solicitar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 'email': emailVal })
                    });
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-green-950/40 text-green-300 border-green-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                        document.getElementById('formConfirmarCancelamento').classList.remove('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro de conexão com o servidor.";
                }
            });

            // SUBMIT: CONFIRMAR CANCELAMENTO
            document.getElementById('formConfirmarCancelamento').addEventListener('submit', async (e) => {
                e.preventDefault();
                const emailVal = document.getElementById('emailCancelamento').value;
                const codigoVal = document.getElementById('codigoSeguranca').value;

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Validando código e cancelando...";

                try {
                    const response = await fetch('/api/cancelar/confirmar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 'email': emailVal, 'codigo': codigoVal })
                    });
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-950 text-red-400 border-red-900/50 text-center text-sm font-bold shadow-inner";
                        feedback.innerText = `🔒 ${dados.mensagem}`;
                        document.getElementById('formSolicitarCancelamento').reset();
                        document.getElementById('formConfirmarCancelamento').reset();
                        document.getElementById('formConfirmarCancelamento').classList.add('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro de conexão com o servidor.";
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/preco/{ativo}")
@app.get("/api/preco")
def obtener_preco_ativo(ativo: str = None):
    if not ativo:
        return JSONResponse(status="erro", mensagem="O código do ativo é obrigatório."), 400

    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker
        
    nome_ativo = ticker.replace(".SA", "")
    tempo_atual = time.time()

    if nome_ativo in CACHE_COTCOES:
        dados_cache = CACHE_COTCOES[nome_ativo]
        if tempo_atual - dados_cache["timestamp"] < CACHE_EXPIRACAO_SEGUNDOS:
            return {
                "status": "sucesso", 
                "ativo": nome_ativo, 
                "preco_atual": dados_cache["preco"],
                "preco": dados_cache["preco"]
            }

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yahoo}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resposta = requests.get(url, headers=headers, timeout=10)
        
        preco_atual = None
        if resposta.status_code == 200:
            dados = resposta.json()
            meta = dados.get("chart", {}).get("result", [{}])[0].get("meta", {})
            preco_atual = meta.get("regularMarketPrice")

        if preco_atual is None:
            dados_acao = yf.Ticker(ticker_yahoo)
            preco_atual = dados_acao.history(period="1d")["Close"].iloc[-1]
            
        preco_final = round(float(preco_atual), 2)
        CACHE_COTCOES[nome_ativo] = {"preco": preco_final, "timestamp": tempo_atual}
        
        return {
            "status": "sucesso",
            "ativo": nome_ativo,
            "preco_atual": preco_final,
            "preco": preco_final
        }
        
    except Exception as e:
        print(f"💥 Erro na API de cotação para {ativo}: {e}")
        if nome_ativo in CACHE_COTCOES:
            return {"status": "sucesso", "ativo": nome_ativo, "preco_atual": CACHE_COTCOES[nome_ativo]["preco"]}
        return {"status": "erro", "mensagem": "Cotação indisponível."}

@app.post("/api/alerta")
@app.post("/api/alerta/")
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

    tempo_atual = time.time()
    preco_atual = None

    if ticker in CACHE_COTCOES:
        dados_cache = CACHE_COTCOES[ticker]
        if tempo_atual - dados_cache["timestamp"] < CACHE_EXPIRACAO_SEGUNDOS:
            preco_atual = dados_cache["preco"]

    if preco_atual is None:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yahoo}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            resposta = requests.get(url, headers=headers, timeout=10)
            if resposta.status_code == 200:
                preco_atual = resposta.json().get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
            if preco_atual is None:
                preco_atual = yf.Ticker(ticker_yahoo).history(period="1d")["Close"].iloc[-1]
            preco_atual = round(float(preco_atual), 2)
            CACHE_COTCOES[ticker] = {"preco": preco_atual, "timestamp": tempo_atual}
        except Exception as e:
            print(f"⚠️ Erro ao buscar cotação de {ticker}: {e}")
            if ticker in CACHE_COTCOES:
                preco_atual = CACHE_COTCOES[ticker]["preco"]
            else:
                return {"status": "erro", "mensagem": "Falha ao validar ativo."}

    novo_alerta = Alerta(email=email.strip().lower(), ativo=ticker, preco_alvo=preco_alvo, condicao=condicao, ativo_sistema=True)
    db.add(novo_alerta)
    db.commit()

    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)

    return {"status": "sucesso", "ativo": ticker, "preco_atual": float(preco_atual), "preco_alvo": float(preco_alvo), "condicao": condicao, "email": novo_alerta.email}


# ROTA: SOLICITAR CÓDIGO DE CANCELAMENTO
@app.post("/api/cancelar/solicitar")
def solicitar_cancelamento(email: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    
    # Busca se o usuário tem monitoramentos ativos
    alertas_ativos = db.query(Alerta).filter(Alerta.email == email_limpo, Alerta.ativo_sistema == True).all()
    
    if not alertas_ativos:
        return {"status": "erro", "mensagem": "Não encontramos nenhum monitoramento ativo para este e-mail."}
        
    # Gera um código aleatório de 6 dígitos
    codigo_seguranca = str(random.randint(100000, 999999))
    
    # Remove códigos antigos deste e-mail para não acumular lixo
    db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo).delete()
    
    # Salva o novo código no banco
    novo_codigo = CodigoCancelamento(email=email_limpo, codigo=codigo_seguranca)
    db.add(novo_codigo)
    db.commit()
    
    # Envia o e-mail com a lista de ativos e o token gerado
    enviar_email_solicitacao_cancelamento(email_limpo, alertas_ativos, codigo_seguranca)
    
    return {"status": "sucesso", "mensagem": "Código enviado! Verifique sua caixa de entrada ou spam."}


# ROTA: CONFIRMAR CÓDIGO E ENCERRAR RADAR
@app.post("/api/cancelar/confirmar")
def confirmar_cancelamento(email: str = Form(...), codigo: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    codigo_limpo = codigo.strip()
    
    # Procura pelo token correspondente no banco
    registro_codigo = db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo, CodigoCancelamento.codigo == codigo_limpo).first()
    
    if not registro_codigo:
        return {"status": "erro", "mensagem": "Código de segurança incorreto ou e-mail inválido."}
        
    # Desativa todos os alertas ativos deste e-mail (muda ativo_sistema para False)
    alertas_desativados = db.query(Alerta).filter(Alerta.email == email_limpo, Alerta.ativo_sistema == True).update({"ativo_sistema": False})
    
    # Deleta o token usado
    db.delete(registro_codigo)
    db.commit()
    
    return {"status": "sucesso", "mensagem": f"Todos os seus monitoramentos ativos ({alertas_desativados}) foram encerrados com sucesso!"}


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
                print(f"📊 Verificando {len(alertas_ativos)} monitoramentos no radar...")
                ativos_unicos = list(set([a.ativo for a in alertas_ativos]))
                cotacoes = {}

                for ativo in ativos_unicos:
                    try:
                        ticker_sa = f"{ativo}.SA"
                        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_sa}"
                        headers = {"User-Agent": "Mozilla/5.0"}
                        resposta = requests.get(url, headers=headers, timeout=10)
                        preco = None
                        if resposta.status_code == 200:
                            preco = resposta.json().get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
                        if preco is None:
                            preco = yf.Ticker(ticker_sa).history(period="1d")["Close"].iloc[-1]
                        
                        if preco is not None:
                            preco_final = round(float(preco), 2)
                            cotacoes[ativo] = preco_final
                            CACHE_COTCOES[ativo] = {"preco": preco_final, "timestamp": time.time()}
                    except Exception as e:
                        print(f"⚠️ Erro no loop para {ativo}: {e}")

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
                        enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                        alerta.ativo_sistema = False
                        db.commit()
        except Exception as e:
            print(f"💥 Erro no monitor: {e}")
        finally:
            db.close()
        time.sleep(300)

thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
thread_robo.start()