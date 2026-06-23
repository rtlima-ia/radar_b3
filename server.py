import os
import time
import random
import threading
from datetime import datetime
from typing import List
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
# Força o uso do domínio novo independente das variáveis antigas do Render
EMAIL_REMETENTE = "alerta@b3alerta.com.br"

# 🟢 CORRIGIDO: Título oficial do app alterado para a nova marca
app = FastAPI(title="B3 Alerta - Radar B3")

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
        "from": f"B3 Alerta <{EMAIL_REMETENTE}>", # 🟢 CORRIGIDO: Nome visual atualizado
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
        f"O B3 Alerta enviará uma mensagem assim que este objetivo for atingido!" # 🟢 Texto atualizado
    )
    enviar_email_via_resend(destino, f"📡 B3 Alerta: Monitoramento {ativo} Ativado!", corpo)

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
    enviar_email_via_resend(destino, f"🔔 B3 Alerta: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

def enviar_email_token_consulta(destino, codigo):
    corpo = (
        f"🔑 SEU CÓDIGO DE ACESSO — B3 ALERTA\n\n"
        f"Você solicitou a consulta dos seus monitoramentos ativos.\n\n"
        f"Utilize o código de segurança abaixo no site para carregar a sua lista de robôs em tempo real:\n"
        f"👉 {codigo} 👈\n\n"
        f"Após inserir este código, você poderá selecionar individualmente quais alertas deseja manter ou desativar.\n"
        f"Se você não solicitou este acesso, apenas ignore este e-mail."
    )
    enviar_email_via_resend(destino, "🔒 Código de Acesso - B3 Alerta", corpo)

# Função auxiliar unificada para puxar preço protegendo o servidor de rate limit
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resposta = requests.get(url, headers=headers, timeout=5)
        preco_atual = None
        if resposta.status_code == 200:
            preco_atual = resposta.json().get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
        if preco_atual is None:
            preco_atual = yf.Ticker(ticker_yahoo).history(period="1d")["Close"].iloc[-1]
        
        preco_final = round(float(preco_atual), 2)
        CACHE_COTCOES[nome_ativo] = {"preco": preco_final, "timestamp": tempo_atual}
        return preco_final
    except Exception:
        if nome_ativo in CACHE_COTCOES:
            return CACHE_COTCOES[nome_ativo]["preco"]
        return 0.0

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
        <title>B3 Alerta - Radar Inteligente</title> <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center justify-center font-sans p-4">

        <div class="max-w-xl w-full bg-slate-900 p-8 rounded-2xl shadow-2xl border border-slate-800">
            <div class="text-center mb-6">
                <h1 class="text-3xl font-extrabold text-green-400">📡 B3 Alerta</h1> <p class="text-slate-400 mt-2 text-sm">Automação inteligente de monitoramento em tempo real.</p>
            </div>

            <div class="flex border-b border-slate-800 mb-6">
                <button id="tabCadastro" class="flex-1 pb-3 text-sm font-bold text-green-400 border-b-2 border-green-400 focus:outline-none">
                    📝 Criar Alerta
                </button>
                <button id="tabCancelamento" class="flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300">
                    🔍 Consultar & Cancelar
                </button>
            </div>

            <form id="formB3" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Código do Ativo (ex: PETR4, VALE3, MXRF11)</label>
                    <div class="relative">
                        <input type="text" id="ativo" placeholder="Digite e clique fora..." required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500 uppercase">
                        <span id="precoTempoReal" class="absolute right-3 top-3 text-xs font-bold text-green-400 hidden"></span>
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
                    Ativar Monitoramento B3 🚀
                </button>
            </form>

            <div id="containerCancelamento" class="space-y-4 hidden">
                <form id="formSolicitarCancelamento" class="space-y-4">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Seu E-mail Cadastrado</label>
                        <input type="email" id="emailCancelamento" placeholder="seuemail@exemplo.com" required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <button type="submit" class="w-full bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 font-bold py-3 px-4 rounded-lg border border-blue-500/30 transition duration-200">
                        Solicitar Código de Consulta 🔑
                    </button>
                </form>

                <form id="formAutenticarConsulta" class="space-y-4 hidden border-t border-slate-800 pt-4">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Insira o Código de 6 Dígitos</label>
                        <input type="text" id="codigoSeguranca" placeholder="Ex: 123456" maxlength="6" required
                            class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-center text-xl font-bold tracking-widest text-white focus:outline-none focus:border-green-500">
                    </div>
                    <button type="submit" class="w-full bg-green-500 text-slate-950 font-bold py-3 px-4 rounded-lg transition duration-200 shadow-lg">
                        Buscar Meus Monitoramentos 🔍
                    </button>
                </form>

                <div id="wrapperListagemAlertas" class="space-y-4 hidden border-t border-slate-800 pt-4">
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider">Selecione o que deseja cancelar:</label>
                    <div id="listaAlertasDinamica" class="space-y-2 max-h-60 overflow-y-auto pr-1">
                        </div>
                    <button id="btnConfirmarCancelamentoLote" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 px-4 rounded-lg transition duration-200 shadow-lg hidden">
                        Cancelar Itens Selecionados 🔒
                    </button>
                </div>
            </div>

            <div id="feedback" class="mt-6 hidden p-5 rounded-xl border"></div>
        </div>

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
                tabCadastro.className = "flex-1 pb-3 text-sm font-bold text-green-400 border-b-2 border-green-400 focus:outline-none";
                tabCancelamento.className = "flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300";
                formB3.classList.remove('hidden');
                containerCancelamento.classList.add('hidden');
                feedback.classList.add('hidden');
            });

            tabCancelamento.addEventListener('click', () => {
                tabCancelamento.className = "flex-1 pb-3 text-sm font-bold text-blue-400 border-b-2 border-blue-400 focus:outline-none";
                tabCadastro.className = "flex-1 pb-3 text-sm font-bold text-slate-500 focus:outline-none hover:text-slate-300";
                formB3.classList.add('hidden');
                containerCancelamento.classList.remove('hidden');
                formSolicitarCancelamento.classList.remove('hidden');
                feedback.classList.add('hidden');
            });

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

            function executarSugestaoCondicao() {
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

            formSolicitarCancelamento.addEventListener('submit', async (e) => {
                e.preventDefault();
                const emailVal = document.getElementById('emailCancelamento').value;

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Validando cadastros e gerando token...";
                feedback.classList.remove('hidden');

                try {
                    const response = await fetch('/api/cancelar/solicitar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 'email': emailVal })
                    });
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/50 text-blue-300 border-blue-800 text-center text-sm font-medium";
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
                const emailVal = document.getElementById('emailCancelamento').value;
                const codigoVal = document.getElementById('codigoSeguranca').value;

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Autenticando e extraindo monitoramentos com cotações em tempo real...";
                feedback.classList.remove('hidden');

                try {
                    const response = await fetch('/api/cancelar/listar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({ 'email': emailVal, 'codigo': codigoVal })
                    });
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        feedback.classList.add('hidden');
                        const listaDiv = document.getElementById('listaAlertasDinamica');
                        listaDiv.innerHTML = "";

                        dados.alertas.forEach(alerta => {
                            const regraTexto = alerta.condicao === "maior" ? "📈 >=" : "📉 <=";
                            const precoAtualTexto = alerta.preco_atual > 0 ? `R$ ${alerta.preco_atual.toFixed(2)}` : "Carregando...";
                            
                            const itemHtml = `
                                <label class="flex items-center justify-between p-3 bg-slate-950 rounded-lg border border-slate-800 hover:border-slate-700 cursor-pointer transition">
                                    <div class="flex items-center gap-3">
                                        <input type="checkbox" value="${alerta.id}" class="w-4 h-4 rounded accent-green-500 cursor-pointer checkbox-alerta-cancelar">
                                        <div class="flex flex-col">
                                            <span class="font-bold text-white tracking-wide uppercase">${alerta.ativo}</span>
                                            <span class="text-[10px] text-slate-500">Mercado: <b class="text-green-400">${precoAtualTexto}</b></span>
                                        </div>
                                    </div>
                                    <div class="text-xs font-semibold text-slate-400 text-right">
                                        Alvo: <span class="text-slate-200">${regraTexto} R$ ${alerta.preco_alvo.toFixed(2)}</span>
                                    </div>
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
                    feedback.innerText = "Código incorreto ou erro de conexão.";
                }
            });

            document.getElementById('btnConfirmarCancelamentoLote').addEventListener('click', async () => {
                const checkboxes = document.querySelectorAll('.checkbox-alerta-cancelar:checked');
                const idsParaCancelar = Array.from(checkboxes).map(cb => cb.value);

                if (idsParaCancelar.length === 0) {
                    alert("Por favor, selecione ao menos um monitoramento da lista para cancelar.");
                    return;
                }

                feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
                feedback.innerText = "Encerrando monitoramentos selecionados...";
                feedback.classList.remove('hidden');

                const emailVal = document.getElementById('emailCancelamento').value;
                const codigoVal = document.getElementById('codigoSeguranca').value;

                try {
                    const response = await fetch('/api/cancelar/confirmar', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({
                            'email': emailVal,
                            'codigo': codigoVal,
                            'ids': idsParaCancelar.join(',')
                        })
                    });
                    const dados = await response.json();

                    if (dados.status === "sucesso") {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-950 text-red-400 border-red-900/50 text-center text-sm font-bold shadow-inner";
                        feedback.innerText = `🔒 ${dados.mensagem}`;
                        
                        document.getElementById('formSolicitarCancelamento').reset();
                        document.getElementById('formAutenticarConsulta').reset();
                        document.getElementById('formAutenticarConsulta').classList.add('hidden');
                        document.getElementById('wrapperListagemAlertas').classList.add('hidden');
                        document.getElementById('btnConfirmarCancelamentoLote').classList.add('hidden');
                    } else {
                        feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                        feedback.innerText = dados.mensagem;
                    }
                } catch (err) {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = "Erro ao processar remoção.";
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

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

    preco_atual = obter_preco_interno(ticker)
    if preco_atual == 0.0:
        return {"status": "erro", "mensagem": "Falha ao validar cotação do ativo."}

    novo_alerta = Alerta(email=email.strip().lower(), ativo=ticker, preco_alvo=preco_alvo, condicao=condicao, ativo_sistema=True)
    db.add(novo_alerta)
    db.commit()

    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)

    return {"status": "sucesso", "ativo": ticker, "preco_atual": preco_atual, "preco_alvo": preco_alvo, "condicao": condicao, "email": novo_alerta.email}

@app.post("/api/cancelar/solicitar")
def solicitar_cancelamento(email: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    alertas_ativos = db.query(Alerta).filter(Alerta.email == email_limpo, Alerta.ativo_sistema == True).all()
    
    if not alertas_ativos:
        return {"status": "erro", "mensagem": "Não encontramos nenhum monitoramento active para este e-mail."}
        
    codigo_seguranca = str(random.randint(100000, 999999))
    db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo).delete()
    
    novo_codigo = CodigoCancelamento(email=email_limpo, codigo=codigo_seguranca)
    db.add(novo_codigo)
    db.commit()
    
    enviar_email_token_consulta(email_limpo, codigo_seguranca)
    return {"status": "sucesso", "mensagem": "Código de consulta enviado com sucesso para a sua caixa de entrada!"}

@app.post("/api/cancelar/listar")
def listar_monitoramentos_usuario(email: str = Form(...), codigo: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    codigo_limpo = codigo.strip()
    
    registro_codigo = db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo, CodigoCancelamento.codigo == codigo_limpo).first()
    if not registro_codigo:
        raise HTTPException(status_code=403, detail="Código inválido ou e-mail incorreto.")
        
    alertas = db.query(Alerta).filter(Alerta.email == email_limpo, Alerta.ativo_sistema == True).all()
    
    lista_alertas = []
    for a in alertas:
        preco_mercado = obter_preco_interno(a.ativo)
        lista_alertas.append({
            "id": a.id, 
            "ativo": a.ativo, 
            "preco_alvo": a.preco_alvo, 
            "condicao": a.condicao,
            "preco_atual": preco_mercado
        })
    
    return {"status": "sucesso", "alertas": lista_alertas}

@app.post("/api/cancelar/confirmar")
def confirmar_cancelamento(email: str = Form(...), codigo: str = Form(...), ids: str = Form(...), db: Session = Depends(get_db)):
    email_limpo = email.strip().lower()
    codigo_limpo = codigo.strip()
    
    registro_codigo = db.query(CodigoCancelamento).filter(CodigoCancelamento.email == email_limpo, CodigoCancelamento.codigo == codigo_limpo).first()
    if not registro_codigo:
        return {"status": "erro", "mensagem": "Token de segurança inválido."}
        
    lista_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not lista_ids:
        return {"status": "erro", "mensagem": "Nenhum monitoramento válido foi selecionado."}
        
    alertas_desativados = db.query(Alerta).filter(
        Alerta.id.in_(lista_ids),
        Alerta.email == email_limpo,
        Alerta.ativo_sistema == True
    ).update({"ativo_sistema": False}, synchronize_session=False)
    
    db.delete(registro_codigo)
    db.commit()
    
    return {"status": "sucesso", "mensagem": f"Sucesso! {alertas_desativados} monitoramento(s) selecionado(s) foi(ram) encerrado(s)."}

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
                    preco = obter_preco_interno(ativo)
                    if preco > 0:
                        cotacoes[ativo] = preco

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