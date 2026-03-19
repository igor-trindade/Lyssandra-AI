import asyncio
import subprocess
import re
import time
import os
from ddgs import DDGS
from ollama import AsyncClient
from google import genai
from google.genai import types
import edge_tts

# ================= CONFIG =================
VOZ = "pt-BR-FranciscaNeural"
RATE = "+15%"
PITCH = "-5Hz"
MODELO_OLLAMA = "lyssandra:latest"
MODELO_GEMINI = "gemini-2.5-flash"
ARQUIVO_MODELFILE = "lyssandra.modelfile"

API_KEY = "AIzaSyC6PYxtNmYhqvDlyxm34sk_huYThY0AJ8A"
cliente_gemini = genai.Client(api_key=API_KEY) if API_KEY else None

# ================= PERSONALIDADE =================
def carregar_personalidade_modelfile(caminho):
    fallback = "Você é Lyssandra. Personalidade: arrogante, possessiva e debochada."
    try:
        if not os.path.exists(caminho):
            return fallback

        with open(caminho, "r", encoding="utf-8") as f:
            conteudo = f.read()

        match = re.search(r'SYSTEM\s+["\']{3}(.*?)["\']{3}', conteudo, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

        match_simples = re.search(r'SYSTEM\s+["\']?(.*?)["\']?\n', conteudo, re.IGNORECASE)
        if match_simples:
            return match_simples.group(1).strip()

        return fallback
    except:
        return fallback

PERSONALIDADE_LYSSANDRA = carregar_personalidade_modelfile(ARQUIVO_MODELFILE)

# ================= LIMPEZA =================
def limpar_texto(texto):
    texto = re.sub(r'\[.*?\]|\(.*?\)', '', texto)
    return texto.replace('*', '').replace('"', '').strip()

# ================= WEB =================
def buscar_na_web(query):
    try:
        with DDGS() as ddgs:
            resultados = list(ddgs.text(query, max_results=3))
            return "\n".join([r['body'] for r in resultados])
    except:
        return "Erro na busca"

# ================= AUDIO (FFPLAY INSANO) =================
async def reproduzir_audio_stream(texto: str):
    if len(texto) < 5:
        return

    processo = await asyncio.create_subprocess_exec(
        "ffplay",
        "-nodisp",
        "-autoexit",
        "-loglevel", "quiet",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-",
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    try:
        comunicador = edge_tts.Communicate(texto, VOZ, rate=RATE, pitch=PITCH)

        primeiro = True

        async for chunk in comunicador.stream():
            if chunk["type"] == "audio" and processo.stdin:

                # 🔥 evita corte inicial
                if primeiro:
                    await asyncio.sleep(0.08)
                    primeiro = False

                processo.stdin.write(chunk["data"])
                await processo.stdin.drain()

    except Exception as e:
        print(f"\n[Erro áudio: {e}]")

    finally:
        if processo.stdin:
            processo.stdin.close()
        await processo.wait()

async def worker_audio(fila):
    while True:
        texto = await fila.get()
        if texto is None:
            break
        await reproduzir_audio_stream(texto)
        fila.task_done()

# ================= STREAM PROCESS =================
async def processar_stream(token, fila, estado):
    estado["buffer"] += token

    if any(p in estado["buffer"] for p in ['. ', '! ', '? ', ', ', '\n']) and len(estado["buffer"]) > 35:
        partes = re.split(r'(?<=[.!?,\n])\s+', estado["buffer"], 1)

        if len(partes) > 1:
            frase = limpar_texto(partes[0])

            if len(frase) > 20:
                estado["acumulador"] += frase + " "

            if len(estado["acumulador"]) > 80:
                await fila.put(estado["acumulador"].strip())
                estado["acumulador"] = ""

            estado["buffer"] = partes[1]

async def finalizar(fila, estado):
    resto = limpar_texto(estado["buffer"])
    if resto:
        estado["acumulador"] += resto

    if estado["acumulador"]:
        await fila.put(estado["acumulador"].strip())

# ================= GEMINI =================
async def processar_gemini(prompt, historico, fila):
    contents = []

    for m in historico:
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))

    instrucao = f"""{PERSONALIDADE_LYSSANDRA}
Mantenha-se sempre na personagem.
"""

    config = types.GenerateContentConfig(
        tools=[{"google_search": {}}],
        system_instruction=instrucao
    )

    stream = await cliente_gemini.aio.models.generate_content_stream(
        model=MODELO_GEMINI,
        contents=contents,
        config=config
    )

    estado = {"buffer": "", "acumulador": ""}
    resposta = ""

    print("Lyssandra: ", end="", flush=True)

    async for chunk in stream:
        if chunk.text:
            print(chunk.text, end="", flush=True)
            resposta += chunk.text
            await processar_stream(chunk.text, fila, estado)

    await finalizar(fila, estado)
    return resposta

# ================= OLLAMA =================
async def processar_ollama(prompt, historico, fila):
    cliente = AsyncClient()
    mensagens = historico + [{'role': 'user', 'content': prompt}]

    stream = await cliente.chat(model=MODELO_OLLAMA, messages=mensagens, stream=True)

    estado = {"buffer": "", "acumulador": ""}
    resposta = ""

    print("Lyssandra (Local): ", end="", flush=True)

    async for chunk in stream:
        token = chunk['message']['content']
        print(token, end="", flush=True)
        resposta += token
        await processar_stream(token, fila, estado)

    await finalizar(fila, estado)
    return resposta

# ================= MAIN =================
async def main():
    print("\n👑 LYSSANDRA\n")

    historico = []
    fila = asyncio.Queue()

    asyncio.create_task(worker_audio(fila))

    while True:
        pergunta = await asyncio.to_thread(input, "\nVocê: ")

        if pergunta.lower() == "sair":
            await fila.put(None)
            break

        try:
            resposta = await processar_gemini(pergunta, historico, fila)
        except Exception as e:
            print(f"\n[Fallback Ollama: {e}]")
            resposta = await processar_ollama(pergunta, historico, fila)

        historico.append({"role": "user", "content": pergunta})
        historico.append({"role": "assistant", "content": resposta})

        print()

if __name__ == "__main__":
    asyncio.run(main())