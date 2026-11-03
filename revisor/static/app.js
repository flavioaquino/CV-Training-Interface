"use strict";

// Mesmas cores do notebook (função desenhar), para a mesma classe ter a mesma cor nos dois lugares.
const CORES = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
               "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#9a6324",
               "#aaffc3", "#800000", "#808000", "#000075"];
const TECLAS = "1234567890qwertyuiop";
const TAM_ALCA = 6;
const LIMIAR_PADRAO = 0.25;
const CHAVE_LIMIAR = "revisor.limiar";

const $ = (id) => document.getElementById(id);
const tela = $("tela");
const ctx = tela.getContext("2d");

const S = {
  classes: [],
  itens: [],          // todas as imagens (metadados vindos do servidor)
  porStem: new Map(),
  lis: new Map(),     // stem -> <li> da lista
  filtrados: [],
  pos: -1,
  stem: null,
  fonte: null,
  caixas: [],         // {c, x, y, w, h, conf, gt}, coordenadas normalizadas (centro, largura, altura)
  historico: [],
  sel: -1,
  ativa: 0,
  sujo: false,
  img: new Image(),
  carregada: false,
  vista: { escala: 1, ox: 0, oy: 0 },
  ocultar: false,
  semTexto: false,
  arrasto: null,
  mouse: null,
  espaco: false,
  clipboard: [],      // caixas copiadas (podem ser coladas nesta ou em outra imagem)
  colarN: 0,
  desenhoPendente: false,
  temConf: false,
  limiarGeral: LIMIAR_PADRAO,
  limiarClasse: [],   // null = usa o geral
  esconderAbaixo: true,
  usarPorClasse: false,
  modeloOk: false,
  modeloPath: "",
  jobId: null,
};

const cor = (c) => CORES[c % CORES.length];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

function contraste(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#000" : "#fff";
}

function limiarDe(classe) {
  if (S.usarPorClasse && S.limiarClasse[classe] != null) return S.limiarClasse[classe];
  return S.limiarGeral;
}

/** Sugestões com confiança abaixo do limiar. Caixas ✓ do backend e caixas sem conf nunca entram. */
function abaixoDoLimiar(b) {
  return !b.gt && b.conf != null && b.conf < limiarDe(b.c);
}

function visivel(b) {
  return !(S.esconderAbaixo && abaixoDoLimiar(b));
}

function fmtConf(v) {
  return v.toFixed(2).replace(".", ",");
}

async function api(metodo, url, corpo) {
  const r = await fetch(url, {
    method: metodo,
    headers: corpo ? { "Content-Type": "application/json" } : {},
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  if (!r.ok) throw new Error(`${metodo} ${url}: ${r.status} ${await r.text()}`);
  return r.json();
}

// ---------------------------------------------------------------- geometria

const iw = () => S.img.naturalWidth;
const ih = () => S.img.naturalHeight;
const paraTela = (px, py) => [px * S.vista.escala + S.vista.ox, py * S.vista.escala + S.vista.oy];
const paraImg = (sx, sy) => [(sx - S.vista.ox) / S.vista.escala, (sy - S.vista.oy) / S.vista.escala];

function paraNorm(sx, sy) {
  const [px, py] = paraImg(sx, sy);
  return [clamp(px / iw(), 0, 1), clamp(py / ih(), 0, 1)];
}

function retTela(b) {
  const [x1, y1] = paraTela((b.x - b.w / 2) * iw(), (b.y - b.h / 2) * ih());
  const [x2, y2] = paraTela((b.x + b.w / 2) * iw(), (b.y + b.h / 2) * ih());
  return { x1, y1, x2, y2 };
}

function deCantos(x1, y1, x2, y2) {
  const [a, b] = [Math.min(x1, x2), Math.max(x1, x2)];
  const [c, d] = [Math.min(y1, y2), Math.max(y1, y2)];
  return { x: (a + b) / 2, y: (c + d) / 2, w: b - a, h: d - c };
}

function alcas(r) {
  const mx = (r.x1 + r.x2) / 2, my = (r.y1 + r.y2) / 2;
  return { nw: [r.x1, r.y1], n: [mx, r.y1], ne: [r.x2, r.y1], e: [r.x2, my],
           se: [r.x2, r.y2], s: [mx, r.y2], sw: [r.x1, r.y2], w: [r.x1, my] };
}

function alcaEm(sx, sy) {
  if (S.sel < 0 || S.ocultar) return null;
  for (const [k, [hx, hy]] of Object.entries(alcas(retTela(S.caixas[S.sel])))) {
    if (Math.abs(sx - hx) <= TAM_ALCA + 2 && Math.abs(sy - hy) <= TAM_ALCA + 2) return k;
  }
  return null;
}

function caixaEm(sx, sy) {
  if (S.ocultar) return -1;
  let melhor = -1, menorArea = Infinity;
  S.caixas.forEach((b, i) => {
    if (!visivel(b)) return;
    const r = retTela(b);
    if (sx >= r.x1 && sx <= r.x2 && sy >= r.y1 && sy <= r.y2) {
      const area = (r.x2 - r.x1) * (r.y2 - r.y1);
      if (area < menorArea) { menorArea = area; melhor = i; }
    }
  });
  return melhor;
}

// ---------------------------------------------------------------- desenho

function pedirDesenho() {
  if (S.desenhoPendente) return;
  S.desenhoPendente = true;
  requestAnimationFrame(() => { S.desenhoPendente = false; desenhar(); });
}

function desenhar() {
  const W = tela.clientWidth, H = tela.clientHeight;
  ctx.fillStyle = "#111";
  ctx.fillRect(0, 0, W, H);
  if (!S.carregada) {
    ctx.fillStyle = "#777";
    ctx.font = "14px system-ui";
    ctx.fillText(S.stem ? "Carregando…" : "Nenhuma imagem neste filtro", 20, 30);
    return;
  }
  const { escala, ox, oy } = S.vista;
  ctx.imageSmoothingEnabled = escala < 2;
  ctx.drawImage(S.img, ox, oy, iw() * escala, ih() * escala);

  if (!S.ocultar) S.caixas.forEach((b, i) => {
    if (i === S.sel || !visivel(b)) return;
    desenharCaixa(b, false);
  });
  if (!S.ocultar && S.sel >= 0 && visivel(S.caixas[S.sel])) desenharCaixa(S.caixas[S.sel], true);

  const a = S.arrasto;
  if (a && a.modo === "desenhar") {
    ctx.strokeStyle = cor(S.ativa);
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 3]);
    ctx.strokeRect(a.sx, a.sy, a.ex - a.sx, a.ey - a.sy);
    ctx.setLineDash([]);
  } else if (S.mouse && !a && !S.ocultar) {
    const [mx, my] = S.mouse;
    ctx.strokeStyle = "#ffffff40";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, my + 0.5); ctx.lineTo(W, my + 0.5);
    ctx.moveTo(mx + 0.5, 0); ctx.lineTo(mx + 0.5, H);
    ctx.stroke();
  }
  $("s-zoom").textContent = `zoom ${Math.round(escala * 100)}%`;
}

function desenharCaixa(b, selecionada) {
  const r = retTela(b), c = cor(b.c);
  const abaixo = abaixoDoLimiar(b);
  ctx.save();
  if (selecionada) {
    ctx.fillStyle = c + "30";
    ctx.fillRect(r.x1, r.y1, r.x2 - r.x1, r.y2 - r.y1);
  }
  ctx.globalAlpha = abaixo && !selecionada ? 0.45 : 1;
  ctx.strokeStyle = c;
  ctx.lineWidth = selecionada ? 3 : b.gt ? 3 : 2;
  if (abaixo) ctx.setLineDash([6, 4]);
  ctx.strokeRect(r.x1, r.y1, r.x2 - r.x1, r.y2 - r.y1);
  ctx.setLineDash([]);

  if (!S.semTexto || selecionada) {
    let txt = b.gt ? "✓ " : "";
    txt += S.classes[b.c] ?? "?";
    if (b.conf != null) txt += ` ${b.conf.toFixed(2)}`;
    ctx.font = "bold 13px system-ui";
    const larg = ctx.measureText(txt).width + 8;
    const ty = r.y1 >= 18 ? r.y1 - 18 : r.y1;
    ctx.fillStyle = c;
    ctx.fillRect(r.x1, ty, larg, 18);
    ctx.fillStyle = contraste(c);
    ctx.fillText(txt, r.x1 + 4, ty + 13);
  }
  if (selecionada) {
    for (const [hx, hy] of Object.values(alcas(r))) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(hx - TAM_ALCA / 2, hy - TAM_ALCA / 2, TAM_ALCA, TAM_ALCA);
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 1;
      ctx.strokeRect(hx - TAM_ALCA / 2, hy - TAM_ALCA / 2, TAM_ALCA, TAM_ALCA);
    }
  }
  ctx.restore();
}

function ajustarVista() {
  if (!S.carregada) return;
  const W = tela.clientWidth, H = tela.clientHeight;
  const e = Math.min(W / iw(), H / ih()) * 0.98;
  S.vista = { escala: e, ox: (W - iw() * e) / 2, oy: (H - ih() * e) / 2 };
}

function redimensionarTela() {
  const r = window.devicePixelRatio || 1;
  tela.width = Math.round(tela.clientWidth * r);
  tela.height = Math.round(tela.clientHeight * r);
  ctx.setTransform(r, 0, 0, r, 0, 0);
  ajustarVista();
  desenhar();
}

// ---------------------------------------------------------------- painéis

function preencherFiltros() {
  const origemAtual = $("f-origem").value;
  const origens = [...new Set(S.itens.map((i) => i.classe_original))].sort();
  $("f-origem").innerHTML = `<option value="">Qualquer</option>` +
    origens.map((o) => `<option value="${esc(o)}">${esc(o || "(sem)")}</option>`).join("");
  if ([...$("f-origem").options].some((o) => o.value === origemAtual)) $("f-origem").value = origemAtual;
  const contemAtual = $("f-contem").value;
  $("f-contem").innerHTML = `<option value="">Qualquer</option>` +
    S.classes.map((c, i) => `<option value="${i}">${esc(c)}</option>`).join("");
  if ([...$("f-contem").options].some((o) => o.value === contemAtual)) $("f-contem").value = contemAtual;
}

function ligarFiltros() {
  for (const id of ["f-status", "f-origem", "f-contem", "f-conflito", "f-importadas", "f-ordem"]) {
    $(id).addEventListener("change", async (ev) => {
      ev.target.blur();  // devolve o teclado para os atalhos
      if (S.sujo && !(await salvar())) return;
      aplicarFiltros();
      if (S.filtrados.length) {
        const pos = S.pos >= 0 ? S.pos : 0;
        abrir(pos);
      } else {
        limpar();
      }
    });
  }
}

function aplicarFiltros() {
  const st = $("f-status").value, org = $("f-origem").value, cont = $("f-contem").value;
  const soConflito = $("f-conflito").checked, soImp = $("f-importadas").checked, ordem = $("f-ordem").value;
  const lista = S.itens.filter((i) =>
    (st === "todas" || (st === "revisadas") === i.revisada) &&
    (!org || i.classe_original === org) &&
    (cont === "" || i.contagem[+cont] > 0) &&
    (!soConflito || i.conflitos > 0) &&
    (!soImp || i.origem === "importada"));
  lista.sort(ordem === "nome" ? (a, b) => a.stem.localeCompare(b.stem) : (a, b) => a.ordem - b.ordem);
  S.filtrados = lista;
  S.pos = lista.findIndex((i) => i.stem === S.stem);
  $("f-contagem").textContent = `${lista.length} imagens neste filtro`;
  renderLista();
}

function renderLista() {
  const ul = $("lista");
  const frag = document.createDocumentFragment();
  S.lis.clear();
  S.filtrados.forEach((it, i) => {
    const li = document.createElement("li");
    li.dataset.i = i;
    li.innerHTML = `<span class="ponto"></span><span class="nome">${esc(it.stem)}</span>` +
      (it.origem === "importada" ? `<span class="tag-nova" title="inserida depois">nova</span>` : "") +
      (it.conflitos ? `<span class="badge" title="sugestões de outra classe em cima da caixa confirmada">${it.conflitos}</span>` : "");
    li.classList.toggle("revisada", it.revisada);
    li.classList.toggle("atual", it.stem === S.stem);
    frag.appendChild(li);
    S.lis.set(it.stem, li);
  });
  ul.replaceChildren(frag);
}

$("lista").addEventListener("click", (ev) => {
  const li = ev.target.closest("li");
  if (li) abrir(+li.dataset.i);
});

function marcarAtualNaLista() {
  for (const li of $("lista").querySelectorAll("li.atual")) li.classList.remove("atual");
  const li = S.lis.get(S.stem);
  if (li) { li.classList.add("atual"); li.scrollIntoView({ block: "nearest" }); }
}

function montarPaleta() {
  $("paleta").innerHTML = S.classes.map((c, i) =>
    `<li data-i="${i}"><span class="cor" style="background:${cor(i)}"></span>` +
    `<kbd>${(TECLAS[i] ?? "").toUpperCase()}</kbd> ${esc(c)} <span class="n"></span></li>`).join("");
}

$("paleta").addEventListener("click", (ev) => {
  const li = ev.target.closest("li");
  if (li) { escolherClasse(+li.dataset.i); pedirDesenho(); }
});

function renderPainel() {
  const n = new Array(S.classes.length).fill(0);
  S.caixas.forEach((b) => { if (visivel(b)) n[b.c]++; });
  $("paleta").querySelectorAll("li").forEach((li, i) => {
    li.classList.toggle("ativa", i === S.ativa);
    li.querySelector(".n").textContent = n[i] || "";
  });

  const abaixo = S.caixas.filter(abaixoDoLimiar).length;
  const vis = S.caixas.filter(visivel).length;
  $("n-caixas").textContent = abaixo
    ? `(${vis} visíveis · ${abaixo} abaixo do limiar)`
    : `(${S.caixas.length})`;
  $("caixas").innerHTML = S.caixas.map((b, i) => {
    const cls = [
      i === S.sel ? "sel" : "",
      abaixoDoLimiar(b) ? "abaixo" : "",
      !visivel(b) ? "escondida" : "",
    ].filter(Boolean).join(" ");
    const info = b.gt ? "✓ backend" : b.conf != null ? b.conf.toFixed(2) : "—";
    return `<li data-i="${i}" class="${cls}"><span class="cor" style="background:${cor(b.c)}"></span>` +
      `${esc(S.classes[b.c])}<span class="info">${info}</span>` +
      `<button class="apagar" data-apagar="${i}" title="Apagar">✕</button></li>`;
  }).join("");

  const it = S.porStem.get(S.stem);
  $("s-stem").textContent = S.stem ? `${S.stem}  ·  ${S.pos + 1}/${S.filtrados.length}` : "";
  $("s-fonte").textContent = !it ? "" : S.fonte === "revisada" ? "✓ revisada" : "pré-anotação (não revisada)";
  $("s-sujo").textContent = S.sujo ? "● alterações não salvas" : "";
  atualizarLimiarInfo();
}

$("caixas").addEventListener("click", (ev) => {
  const apagar = ev.target.closest("[data-apagar]");
  if (apagar) { S.sel = +apagar.dataset.apagar; apagarSelecionada(); return; }
  const li = ev.target.closest("li");
  if (li) { S.sel = +li.dataset.i; renderPainel(); pedirDesenho(); }
});

function atualizarProgresso() {
  const feitas = S.itens.filter((i) => i.revisada).length;
  $("barra-cheia").style.width = `${(100 * feitas) / Math.max(1, S.itens.length)}%`;
  $("progresso-texto").textContent = `${feitas} de ${S.itens.length} revisadas`;
}

function atualizarItem(novo) {
  const it = S.porStem.get(novo.stem);
  Object.assign(it, novo);
  const li = S.lis.get(novo.stem);
  if (li) li.classList.toggle("revisada", it.revisada);
  atualizarProgresso();
}

// ---------------------------------------------------------------- imagem atual

function limpar() {
  S.stem = null; S.caixas = []; S.carregada = false; S.pos = -1; S.sel = -1; S.sujo = false;
  renderPainel(); desenhar();
}

async function abrir(pos) {
  if (pos < 0 || pos >= S.filtrados.length) return;
  if (S.sujo && !(await salvar())) return;
  const it = S.filtrados[pos];
  S.pos = pos;
  S.stem = it.stem;
  let dados;
  try {
    dados = await api("GET", `/api/imagens/${encodeURIComponent(it.stem)}`);
  } catch (e) {
    alert(e.message);
    return;
  }
  if (S.stem !== it.stem) return;  // o usuário já foi para outra imagem

  S.caixas = dados.caixas;
  S.fonte = dados.fonte;
  S.historico = [];
  S.sel = -1;
  S.sujo = false;
  S.carregada = false;
  const img = new Image();
  img.onload = () => {
    if (S.stem !== it.stem) return;
    S.img = img;
    S.carregada = true;
    ajustarVista();
    pedirDesenho();
  };
  img.src = `/img/${encodeURIComponent(it.stem)}`;
  if (S.filtrados[pos + 1]) new Image().src = `/img/${encodeURIComponent(S.filtrados[pos + 1].stem)}`;
  marcarAtualNaLista();
  renderPainel();
  pedirDesenho();
}

async function salvar(avancar = false) {
  if (!S.stem) return false;
  const stem = S.stem;
  const caixas = S.caixas.map(({ c, x, y, w, h, conf, gt }) => ({ c, x, y, w, h, conf: conf ?? null, gt: !!gt }));
  try {
    atualizarItem(await api("PUT", `/api/imagens/${encodeURIComponent(stem)}`, { caixas }));
  } catch (e) {
    alert(`Não foi possível salvar ${stem}:\n${e.message}`);
    return false;
  }
  if (S.stem === stem) { S.sujo = false; S.fonte = "revisada"; renderPainel(); }
  if (avancar) {
    if (S.pos + 1 < S.filtrados.length) abrir(S.pos + 1);
    else alert("Última imagem deste filtro.");
  }
  return true;
}

async function restaurar() {
  if (!S.stem) return;
  if (!confirm("Descartar a revisão desta imagem e voltar às sugestões da pré-anotação?")) return;
  atualizarItem(await api("DELETE", `/api/imagens/${encodeURIComponent(S.stem)}`));
  S.sujo = false;
  abrir(S.pos);
}

// ---------------------------------------------------------------- edição

function guardar() {
  S.historico.push(structuredClone(S.caixas));
  if (S.historico.length > 200) S.historico.shift();
}

function marcarSujo() {
  S.sujo = true;
  renderPainel();
}

function desfazer() {
  if (!S.historico.length) return;
  S.caixas = S.historico.pop();
  S.sel = -1;
  marcarSujo();
  pedirDesenho();
}

function copiarSelecionada() {
  const origens = S.sel >= 0 ? [S.caixas[S.sel]] : S.caixas.filter(visivel);
  if (!origens.length) return;
  S.clipboard = origens.map(({ c, x, y, w, h }) => ({ c, x, y, w, h }));
  S.colarN = 0;
}

function colarCaixas() {
  if (!S.clipboard.length || !S.stem) return;
  S.colarN += 1;
  const d = 0.02 * S.colarN;
  guardar();
  for (const b of S.clipboard) {
    const w = b.w, h = b.h;
    S.caixas.push({
      c: b.c,
      x: clamp(b.x + d, w / 2, 1 - w / 2),
      y: clamp(b.y + d, h / 2, 1 - h / 2),
      w, h,
      conf: null,
      gt: false,
    });
  }
  S.sel = S.caixas.length - 1;
  marcarSujo();
}

function apagarSelecionada() {
  if (S.sel < 0) return;
  guardar();
  S.caixas.splice(S.sel, 1);
  S.sel = -1;
  marcarSujo();
  pedirDesenho();
}

function escolherClasse(i) {
  if (i >= S.classes.length) return;
  S.ativa = i;
  const b = S.caixas[S.sel];
  if (b && b.c !== i) {
    guardar();
    b.c = i;
    b.conf = null;
    b.gt = false;
    marcarSujo();
  }
  renderPainel();
}

function redimensionarCaixa(a, sx, sy) {
  const [mx, my] = paraNorm(sx, sy);
  const o = a.orig;
  let x1 = o.x - o.w / 2, x2 = o.x + o.w / 2, y1 = o.y - o.h / 2, y2 = o.y + o.h / 2;
  if (a.alca.includes("w")) x1 = mx;
  if (a.alca.includes("e")) x2 = mx;
  if (a.alca.includes("n")) y1 = my;
  if (a.alca.includes("s")) y2 = my;
  Object.assign(S.caixas[S.sel], deCantos(x1, y1, x2, y2));
}

// ---------------------------------------------------------------- mouse

function posMouse(ev) {
  const r = tela.getBoundingClientRect();
  return [ev.clientX - r.left, ev.clientY - r.top, ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom];
}

const CURSORES = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
                   n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };

function atualizarCursor(sx, sy, shift) {
  if (S.espaco) { tela.style.cursor = "grab"; return; }
  if (shift) { tela.style.cursor = "crosshair"; return; }
  const alca = alcaEm(sx, sy);
  tela.style.cursor = alca ? CURSORES[alca] : caixaEm(sx, sy) >= 0 ? "move" : "crosshair";
}

tela.addEventListener("contextmenu", (ev) => ev.preventDefault());

tela.addEventListener("mousedown", (ev) => {
  if (!S.carregada) return;
  const [sx, sy] = posMouse(ev);
  if (ev.button === 1 || ev.button === 2 || (ev.button === 0 && S.espaco)) {
    S.arrasto = { modo: "pan", sx, sy, ox: S.vista.ox, oy: S.vista.oy };
    tela.style.cursor = "grabbing";
    ev.preventDefault();
    return;
  }
  if (ev.button !== 0 || S.ocultar) return;

  // Shift: sempre desenha uma caixa nova, mesmo em cima de uma que já existe.
  if (ev.shiftKey) {
    S.sel = -1;
    S.arrasto = { modo: "desenhar", sx, sy, ex: sx, ey: sy };
    renderPainel();
    pedirDesenho();
    return;
  }

  const alca = alcaEm(sx, sy);
  if (alca) {
    guardar();
    S.arrasto = { modo: "redim", alca, orig: { ...S.caixas[S.sel] }, moveu: false };
    return;
  }
  const i = caixaEm(sx, sy);
  if (i >= 0) {
    S.sel = i;
    guardar();
    S.arrasto = { modo: "mover", orig: { ...S.caixas[i] }, sx, sy, moveu: false };
  } else {
    S.sel = -1;
    S.arrasto = { modo: "desenhar", sx, sy, ex: sx, ey: sy };
  }
  renderPainel();
  pedirDesenho();
});

window.addEventListener("mousemove", (ev) => {
  const [sx, sy, dentro] = posMouse(ev);
  S.mouse = dentro ? [sx, sy] : null;
  const a = S.arrasto;
  if (!a) {
    if (dentro && S.carregada) { atualizarCursor(sx, sy, ev.shiftKey); pedirDesenho(); }
    return;
  }
  if (a.modo === "pan") {
    S.vista.ox = a.ox + sx - a.sx;
    S.vista.oy = a.oy + sy - a.sy;
  } else if (a.modo === "desenhar") {
    a.ex = sx;
    a.ey = sy;
  } else if (a.modo === "mover") {
    const b = S.caixas[S.sel];
    const dx = (sx - a.sx) / S.vista.escala / iw(), dy = (sy - a.sy) / S.vista.escala / ih();
    b.x = clamp(a.orig.x + dx, b.w / 2, 1 - b.w / 2);
    b.y = clamp(a.orig.y + dy, b.h / 2, 1 - b.h / 2);
    a.moveu = a.moveu || dx !== 0 || dy !== 0;
  } else if (a.modo === "redim") {
    redimensionarCaixa(a, sx, sy);
    a.moveu = true;
  }
  pedirDesenho();
});

window.addEventListener("mouseup", () => {
  const a = S.arrasto;
  if (!a) return;
  S.arrasto = null;
  if (a.modo === "desenhar") {
    if (Math.abs(a.ex - a.sx) > 4 && Math.abs(a.ey - a.sy) > 4) {
      const [x1, y1] = paraNorm(a.sx, a.sy), [x2, y2] = paraNorm(a.ex, a.ey);
      guardar();
      S.caixas.push({ c: S.ativa, ...deCantos(x1, y1, x2, y2), conf: null, gt: false });
      S.sel = S.caixas.length - 1;
      marcarSujo();
    }
  } else if (a.modo === "mover" || a.modo === "redim") {
    if (a.moveu) {
      S.caixas[S.sel].conf = null;  // a caixa agora foi conferida por você
      marcarSujo();
    } else {
      S.historico.pop();
    }
  }
  renderPainel();
  pedirDesenho();
});

tela.addEventListener("wheel", (ev) => {
  if (!S.carregada) return;
  ev.preventDefault();
  const [sx, sy] = posMouse(ev);
  const nova = clamp(S.vista.escala * Math.exp(-ev.deltaY * 0.0015), 0.05, 20);
  const k = nova / S.vista.escala;
  S.vista.ox = sx - (sx - S.vista.ox) * k;
  S.vista.oy = sy - (sy - S.vista.oy) * k;
  S.vista.escala = nova;
  pedirDesenho();
}, { passive: false });

// ---------------------------------------------------------------- teclado

function dialogoAberto() {
  return $("ajuda").open || $("dlg-importar").open || $("dlg-versoes").open || $("dlg-modelos").open;
}

window.addEventListener("keydown", (ev) => {
  if (dialogoAberto() || (ev.target instanceof Element && ev.target.matches("input, select, textarea"))) return;
  const k = ev.key.toLowerCase();
  if (ev.ctrlKey || ev.metaKey) {
    if (k === "z") { desfazer(); ev.preventDefault(); }
    else if (k === "s") { salvar(); ev.preventDefault(); }
    else if (k === "c") { copiarSelecionada(); ev.preventDefault(); }
    else if (k === "v") { colarCaixas(); ev.preventDefault(); pedirDesenho(); }
    return;
  }
  if (ev.key === "Shift" && S.mouse) atualizarCursor(S.mouse[0], S.mouse[1], true);
  if (ev.altKey) return;
  switch (k) {
    case "enter": salvar(true); break;
    case "d": case "arrowright": abrir(S.pos + 1); break;
    case "a": case "arrowleft": abrir(S.pos - 1); break;
    case "delete": case "backspace": apagarSelecionada(); break;
    case "escape": S.sel = -1; renderPainel(); break;
    case "tab": {
      const idxs = S.caixas.map((b, i) => (visivel(b) ? i : -1)).filter((i) => i >= 0);
      if (idxs.length) {
        const cur = idxs.indexOf(S.sel);
        const next = idxs[(cur + (ev.shiftKey ? idxs.length - 1 : 1) + idxs.length) % idxs.length];
        S.sel = next;
      }
      renderPainel();
      break;
    }
    case "h": S.ocultar = !S.ocultar; if (S.ocultar) S.sel = -1; break;
    case "l": S.semTexto = !S.semTexto; break;
    case "f": ajustarVista(); break;
    case " ": S.espaco = true; tela.style.cursor = "grab"; break;
    case "?": $("ajuda").showModal(); break;
    default: {
      const i = TECLAS.indexOf(k);
      if (i >= 0) escolherClasse(i);
      else return;
    }
  }
  ev.preventDefault();
  pedirDesenho();
});

window.addEventListener("keyup", (ev) => {
  if (ev.key === " ") { S.espaco = false; tela.style.cursor = "crosshair"; }
  if (ev.key === "Shift" && S.mouse && !S.arrasto) atualizarCursor(S.mouse[0], S.mouse[1], false);
});

window.addEventListener("beforeunload", (ev) => {
  if (S.sujo) ev.preventDefault();
});

$("btn-salvar").addEventListener("click", () => salvar(true));
$("btn-desfazer").addEventListener("click", desfazer);
$("btn-restaurar").addEventListener("click", restaurar);
$("btn-ajuda").addEventListener("click", () => $("ajuda").showModal());
for (const b of document.querySelectorAll("button")) b.addEventListener("mouseup", () => b.blur());

// ---------------------------------------------------------------- limiar

function carregarLimiar() {
  try {
    const salvos = JSON.parse(localStorage.getItem(CHAVE_LIMIAR) || "null");
    if (!salvos) return;
    if (typeof salvos.geral === "number") S.limiarGeral = clamp(salvos.geral, 0, 1);
    if (Array.isArray(salvos.classe) && salvos.classe.length === S.classes.length) {
      S.limiarClasse = salvos.classe.map((v) => (v == null ? null : clamp(+v, 0, 1)));
    }
    if (typeof salvos.esconder === "boolean") S.esconderAbaixo = salvos.esconder;
    if (typeof salvos.porClasse === "boolean") S.usarPorClasse = salvos.porClasse;
  } catch { /* ignore */ }
}

function salvarLimiar() {
  localStorage.setItem(CHAVE_LIMIAR, JSON.stringify({
    geral: S.limiarGeral,
    classe: S.limiarClasse,
    esconder: S.esconderAbaixo,
    porClasse: S.usarPorClasse,
  }));
}

function montarLimiar() {
  S.limiarClasse = S.classes.map((_, i) => S.limiarClasse[i] ?? null);
  $("limiar-geral").value = Math.round(S.limiarGeral * 100);
  $("limiar-geral-val").textContent = fmtConf(S.limiarGeral);
  $("limiar-esconder").checked = S.esconderAbaixo;
  $("limiar-por-classe").checked = S.usarPorClasse;
  $("limiar-classes").hidden = !S.usarPorClasse;
  $("limiar-classes").innerHTML = S.classes.map((nome, i) => {
    const custom = S.limiarClasse[i] != null;
    const val = custom ? S.limiarClasse[i] : S.limiarGeral;
    return `<li data-i="${i}">` +
      `<input type="checkbox" class="lc-on" title="Usar limiar próprio" ${custom ? "checked" : ""}>` +
      `<span><span class="cor" style="background:${cor(i)};display:inline-block;vertical-align:middle;margin-right:4px"></span>${esc(nome)}</span>` +
      `<input type="range" class="lc-range" min="0" max="100" step="1" value="${Math.round(val * 100)}" ${custom ? "" : "disabled"}>` +
      `<span class="lc-val">${fmtConf(val)}</span></li>`;
  }).join("");

  const aviso = $("aviso-conf");
  if (!S.temConf) {
    aviso.hidden = false;
    aviso.innerHTML = "Ainda não há confianças gravadas. No terminal, com o revisor parado:<br>" +
      "<code>.venv\\Scripts\\python -m revisor gerar-conf</code><br>Depois reinicie o revisor.";
  } else {
    aviso.hidden = true;
  }
}

function atualizarLimiarInfo() {
  const abaixo = S.caixas.filter(abaixoDoLimiar).length;
  const comConf = S.caixas.filter((b) => b.conf != null).length;
  $("limiar-info").textContent = !S.temConf
    ? ""
    : `${comConf} com confiança · limiar geral ${fmtConf(S.limiarGeral)}` +
      (abaixo ? ` · ${abaixo} abaixo` : "");
}

function limiarMudou() {
  if (S.sel >= 0 && S.caixas[S.sel] && !visivel(S.caixas[S.sel])) S.sel = -1;
  salvarLimiar();
  renderPainel();
  pedirDesenho();
}

$("limiar-geral").addEventListener("input", (ev) => {
  S.limiarGeral = +ev.target.value / 100;
  $("limiar-geral-val").textContent = fmtConf(S.limiarGeral);
  // atualiza valores exibidos das classes que herdaram o geral
  $("limiar-classes").querySelectorAll("li").forEach((li) => {
    const i = +li.dataset.i;
    if (S.limiarClasse[i] == null) {
      li.querySelector(".lc-range").value = ev.target.value;
      li.querySelector(".lc-val").textContent = fmtConf(S.limiarGeral);
    }
  });
  limiarMudou();
});
$("limiar-esconder").addEventListener("change", (ev) => {
  S.esconderAbaixo = ev.target.checked;
  limiarMudou();
});
$("limiar-por-classe").addEventListener("change", (ev) => {
  S.usarPorClasse = ev.target.checked;
  $("limiar-classes").hidden = !S.usarPorClasse;
  limiarMudou();
});
$("limiar-classes").addEventListener("input", (ev) => {
  const li = ev.target.closest("li");
  if (!li) return;
  const i = +li.dataset.i;
  if (ev.target.classList.contains("lc-on")) {
    if (ev.target.checked) {
      S.limiarClasse[i] = S.limiarGeral;
      li.querySelector(".lc-range").disabled = false;
    } else {
      S.limiarClasse[i] = null;
      li.querySelector(".lc-range").disabled = true;
      li.querySelector(".lc-range").value = Math.round(S.limiarGeral * 100);
    }
  } else if (ev.target.classList.contains("lc-range")) {
    S.limiarClasse[i] = +ev.target.value / 100;
  }
  const val = S.limiarClasse[i] != null ? S.limiarClasse[i] : S.limiarGeral;
  li.querySelector(".lc-val").textContent = fmtConf(val);
  limiarMudou();
});

function aplicarLimiarNestaImagem() {
  const idxs = S.caixas.map((b, i) => (abaixoDoLimiar(b) ? i : -1)).filter((i) => i >= 0);
  if (!idxs.length) {
    alert("Nenhuma sugestão abaixo do limiar nesta imagem.");
    return;
  }
  if (!confirm(`Apagar ${idxs.length} sugestão(ões) abaixo do limiar nesta imagem?\n` +
               `(Caixas ✓ do backend e caixas sem confiança ficam.)`)) return;
  guardar();
  for (let i = idxs.length - 1; i >= 0; i--) S.caixas.splice(idxs[i], 1);
  S.sel = -1;
  marcarSujo();
  pedirDesenho();
}
$("btn-aplicar-limiar").addEventListener("click", aplicarLimiarNestaImagem);

// ---------------------------------------------------------------- recarregar índice após import/versão

async function recarregarItens({ reabrir = false } = {}) {
  const cfg = await api("GET", "/api/config");
  S.temConf = !!cfg.tem_conf;
  S.modeloOk = !!cfg.modelo_ok;
  S.modeloPath = cfg.modelo || "";
  S.itens = await api("GET", "/api/imagens");
  S.porStem = new Map();
  S.itens.forEach((it) => S.porStem.set(it.stem, it));
  preencherFiltros();
  aplicarFiltros();
  atualizarProgresso();
  if (reabrir) {
    if (S.stem && S.pos >= 0) {
      S.sujo = false;
      await abrir(S.pos);
    } else if (S.filtrados.length) {
      await abrir(0);
    } else {
      limpar();
    }
  }
}

// ---------------------------------------------------------------- importar fotos

function fmtConfRange(el, out) {
  const v = +el.value / 100;
  out.textContent = fmtConf(v);
  return v;
}

$("imp-conf").addEventListener("input", () => fmtConfRange($("imp-conf"), $("imp-conf-val")));
$("imp-files").addEventListener("change", () => {
  const n = $("imp-files").files.length;
  $("imp-n").textContent = n ? `${n} arquivo(s) selecionado(s)` : "";
});

const drop = $("imp-drop");
drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (ev) => {
  ev.preventDefault();
  drop.classList.remove("over");
  const dt = new DataTransfer();
  for (const f of $("imp-files").files) dt.items.add(f);
  for (const f of ev.dataTransfer.files) dt.items.add(f);
  $("imp-files").files = dt.files;
  $("imp-n").textContent = `${dt.files.length} arquivo(s) selecionado(s)`;
});

function infoDataset(d) {
  if (!d) return "";
  const ja = d.n_ja_no_revisor ? ` · ${d.n_ja_no_revisor} já no revisor` : "";
  const cls = d.classes?.length ? d.classes.join(", ") : "(sem data.yaml)";
  return `${d.n_imagens} fotos · ${d.n_labels} rótulos · classes: ${cls}${ja}`;
}

async function carregarDatasets() {
  const sel = $("imp-ds");
  let lista = [];
  try {
    lista = await api("GET", "/api/datasets");
  } catch (e) {
    $("imp-ds-info").textContent = e.message;
    return;
  }
  if (!lista.length) {
    sel.innerHTML = `<option value="">Nenhum export YOLO na pasta do projeto</option>`;
    $("imp-ds-info").textContent = "";
    $("btn-imp-ds").disabled = true;
    return;
  }
  sel.innerHTML = lista.map((d) =>
    `<option value="${esc(d.pasta)}">${esc(d.pasta)}</option>`).join("");
  $("btn-imp-ds").disabled = false;
  sel.onchange = () => {
    const d = lista.find((x) => x.pasta === sel.value);
    $("imp-ds-info").textContent = infoDataset(d);
  };
  sel.dispatchEvent(new Event("change"));
}

function abrirImportar() {
  $("imp-status").textContent = "";
  $("imp-modelo").textContent = S.modeloOk
    ? `Modelo: ${S.modeloPath}`
    : `Modelo não encontrado (${S.modeloPath}). Dá para inserir as fotos e pré-classificar depois.`;
  $("imp-preclass").disabled = !S.modeloOk;
  if (!S.modeloOk) $("imp-preclass").checked = false;
  $("dlg-importar").showModal();
  carregarDatasets();
}

async function aposImportar(n) {
  $("f-importadas").checked = true;
  $("f-status").value = "pendentes";
  await recarregarItens({ reabrir: true });
  if (n) $("imp-status").textContent += ` · filtro: fotos inseridas depois (${n})`;
}

$("btn-importar").addEventListener("click", abrirImportar);
$("btn-imp-fechar").addEventListener("click", () => $("dlg-importar").close());
$("btn-imp-ds").addEventListener("click", async () => {
  const pasta = $("imp-ds").value;
  if (!pasta) {
    $("imp-status").textContent = "Escolha um dataset na lista.";
    return;
  }
  $("btn-imp-ds").disabled = true;
  $("imp-status").textContent = "Importando (hard link das fotos, sem copiar os  GB)…";
  let r;
  try {
    r = await api("POST", "/api/importar-dataset", {
      pasta,
      preclassificar: $("imp-preclass").checked,
      conf: +$("imp-conf").value / 100,
    });
  } catch (e) {
    $("imp-status").textContent = "Falha: " + e.message;
    $("btn-imp-ds").disabled = false;
    return;
  }
  const extra = [];
  if (r.puladas?.length) extra.push(`${r.puladas.length} já estavam no revisor`);
  if (r.classes_ignoradas?.length) extra.push(`classes sem mapa: ${r.classes_ignoradas.join(", ")}`);
  $("imp-status").textContent =
    `${r.inseridas.length} foto(s) · ${r.n_gt} caixa(s) do backend` +
    (extra.length ? ` · ${extra.join(" · ")}` : "");
  await aposImportar(r.inseridas.length);
  if (r.job_id) await acompanharJob(r.job_id);
  $("btn-imp-ds").disabled = false;
  carregarDatasets();
});
$("btn-imp-preclass").addEventListener("click", async () => {
  if (!S.modeloOk) {
    $("imp-status").textContent = "Modelo não encontrado; não dá para pré-classificar.";
    return;
  }
  const stems = S.itens.filter((i) => i.origem === "importada" && !i.revisada).map((i) => i.stem);
  if (!stems.length) {
    $("imp-status").textContent = "Nenhuma foto nova pendente de revisão.";
    return;
  }
  $("btn-imp-preclass").disabled = true;
  try {
    const r = await api("POST", "/api/preclassificar", {
      stems,
      conf: +$("imp-conf").value / 100,
    });
    await acompanharJob(r.job_id);
  } catch (e) {
    $("imp-status").textContent = e.message;
  }
  $("btn-imp-preclass").disabled = false;
});
$("imp-preclass").addEventListener("change", () => {
  $("imp-conf-linha").hidden = !$("imp-preclass").checked;
});

$("form-importar").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const files = $("imp-files").files;
  if (!files.length) {
    $("imp-status").textContent = "Escolha pelo menos uma foto.";
    return;
  }
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("preclassificar", $("imp-preclass").checked ? "true" : "false");
  fd.append("conf", String(+$("imp-conf").value / 100));
  $("btn-imp-ok").disabled = true;
  $("imp-status").textContent = "Enviando…";
  let r;
  try {
    const resp = await fetch("/api/importar", { method: "POST", body: fd });
    if (!resp.ok) throw new Error(await resp.text());
    r = await resp.json();
  } catch (e) {
    $("imp-status").textContent = "Falha: " + e.message;
    $("btn-imp-ok").disabled = false;
    return;
  }
  const extra = [];
  if (r.ignoradas?.length) extra.push(`ignoradas: ${r.ignoradas.join(", ")}`);
  if (r.erros?.length) extra.push(r.erros.join("; "));
  $("imp-status").textContent =
    `${r.inseridas.length} foto(s) inserida(s)` + (extra.length ? ` · ${extra.join(" · ")}` : "");
  $("imp-files").value = "";
  $("imp-n").textContent = "";
  await recarregarItens();
  if (r.job_id) await acompanharJob(r.job_id);
  $("btn-imp-ok").disabled = false;
});

function mostrarJob(j) {
  const banner = $("job-banner");
  if (!j || j.status === "ok" || j.status === "erro") {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  $("job-msg").textContent = j.msg || "pré-classificando…";
  const pct = j.total ? Math.round(100 * j.feito / j.total) : 0;
  $("job-bar-cheia").style.width = `${pct}%`;
}

async function acompanharJob(jid) {
  S.jobId = jid;
  for (;;) {
    let j;
    try {
      j = await api("GET", `/api/jobs/${jid}`);
    } catch (e) {
      $("imp-status").textContent = "Job: " + e.message;
      mostrarJob(null);
      return;
    }
    mostrarJob(j);
    $("imp-status").textContent = j.msg || j.status;
    if (j.status === "ok") {
      await recarregarItens({ reabrir: S.stem && (j.stems || []).includes(S.stem) });
      $("imp-status").textContent =
        `Pré-classificação ok · ${j.n_caixas} caixa(s) em ${j.stems?.length ?? 0} foto(s)`;
      mostrarJob(null);
      return j;
    }
    if (j.status === "erro") {
      $("imp-status").textContent = "Pré-classificação falhou: " + (j.erro || "");
      mostrarJob(null);
      return j;
    }
    await new Promise((res) => setTimeout(res, 400));
  }
}

// ---------------------------------------------------------------- versões

async function renderVersoes() {
  const itens = await api("GET", "/api/versoes");
  const ul = $("lista-versoes");
  if (!itens.length) {
    ul.innerHTML = `<li class="ver-meta">Nenhuma versão ainda.</li>`;
    return;
  }
  ul.innerHTML = itens.slice().reverse().map((v) =>
    `<li data-id="${esc(v.id)}">` +
      `<div><strong>${esc(v.id)} · ${esc(v.nome)}</strong>` +
      `<div class="ver-meta">${esc(v.criada_em)} · ${v.n_revisadas} revisadas · ${v.n_caixas_revisadas} caixas · ${v.n_pre} pré-anotações` +
      (v.nota ? `<br>${esc(v.nota)}` : "") + `</div></div>` +
      `<button type="button" data-restaurar="${esc(v.id)}">Restaurar</button>` +
      `<button type="button" class="perigo" data-apagar="${esc(v.id)}">Apagar</button>` +
    `</li>`
  ).join("");
}

$("btn-versoes").addEventListener("click", async () => {
  $("ver-status").textContent = "";
  $("dlg-versoes").showModal();
  try { await renderVersoes(); } catch (e) { $("ver-status").textContent = e.message; }
});

$("form-versao").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("btn-ver-criar").disabled = true;
  try {
    const v = await api("POST", "/api/versoes", {
      nome: $("ver-nome").value.trim(),
      nota: $("ver-nota").value.trim(),
    });
    $("ver-nome").value = "";
    $("ver-nota").value = "";
    $("ver-status").textContent = `Criada ${v.id} (${v.n_revisadas} revisadas).`;
    await renderVersoes();
  } catch (e) {
    $("ver-status").textContent = "Não criou: " + e.message;
  }
  $("btn-ver-criar").disabled = false;
});

$("lista-versoes").addEventListener("click", async (ev) => {
  const apagar = ev.target.closest("[data-apagar]");
  const restaurar = ev.target.closest("[data-restaurar]");
  if (apagar) {
    const id = apagar.dataset.apagar;
    if (!confirm(`Apagar a versão ${id}? Isso não mexe nos rótulos atuais, só no snapshot.`)) return;
    try {
      await api("DELETE", `/api/versoes/${encodeURIComponent(id)}`);
      $("ver-status").textContent = `Apagada ${id}.`;
      await renderVersoes();
    } catch (e) {
      $("ver-status").textContent = e.message;
    }
    return;
  }
  if (restaurar) {
    const id = restaurar.dataset.restaurar;
    if (S.sujo && !(await salvar())) return;
    if (!confirm(
      `Restaurar ${id}?\nOs rótulos das fotos que já existiam naquela versão voltam ao snapshot.\n` +
      `Fotos inseridas depois continuam no dataset com os rótulos atuais.`
    )) return;
    try {
      await api("POST", `/api/versoes/${encodeURIComponent(id)}/restaurar`);
      $("ver-status").textContent = `Restaurada ${id}.`;
      await recarregarItens({ reabrir: true });
      await renderVersoes();
    } catch (e) {
      $("ver-status").textContent = e.message;
    }
  }
});

// ---------------------------------------------------------------- modelos (treino / métricas / teste)

const CORES_TST = CORES;
let TST = { img: null, caixas: [], classes: [] };
let TRN_JOB = null;

function fmtNum(v, casas = 3) {
  if (v == null || Number.isNaN(+v)) return "—";
  return (+v).toFixed(casas).replace(".", ",");
}

document.querySelectorAll("#dlg-modelos .aba").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#dlg-modelos .aba").forEach((b) => b.classList.toggle("ativa", b === btn));
    for (const id of ["treino", "exps", "teste"]) {
      $(`aba-${id}`).hidden = btn.dataset.aba !== id;
    }
    if (btn.dataset.aba === "exps") carregarExperimentos();
    if (btn.dataset.aba === "teste") preencherTesteModelos();
  });
});

async function abrirModelos() {
  $("trn-status").textContent = "";
  $("dlg-modelos").showModal();
  document.querySelector('#dlg-modelos .aba[data-aba="treino"]').click();
  try {
    const r = await api("GET", "/api/treino/resumo");
    $("trn-resumo").textContent =
      `${r.n_revisadas} revisadas prontas para treino` +
      ` · ${r.n_pendentes} ainda sem revisão` +
      ` · ${r.classes.length} classes`;
    $("trn-base").innerHTML = (r.bases || []).map((b) =>
      `<option value="${esc(b.id)}">${esc(b.nome)} (${esc(b.tipo)})</option>`).join("");
    if (r.jobs_ativos?.length) {
      TRN_JOB = r.jobs_ativos[0].id;
      acompanharTreino(TRN_JOB);
    }
  } catch (e) {
    $("trn-resumo").textContent = e.message;
  }
}
$("btn-modelos").addEventListener("click", abrirModelos);

$("btn-trn-start").addEventListener("click", async () => {
  if (!confirm("Montar o dataset com as fotos revisadas e iniciar o treino?\nIsso usa a GPU e pode levar horas.")) return;
  $("btn-trn-start").disabled = true;
  $("trn-status").textContent = "Iniciando…";
  try {
    const job = await api("POST", "/api/treino", {
      nome: $("trn-nome").value.trim(),
      base: $("trn-base").value,
      epochs: +$("trn-epochs").value,
      patience: +$("trn-patience").value,
      imgsz: +$("trn-imgsz").value,
      batch: +$("trn-batch").value,
      fracao_val: +$("trn-fracao").value,
      workers: 0,
    });
    TRN_JOB = job.id;
    $("trn-status").textContent = `Job ${job.id} iniciado.`;
    acompanharTreino(job.id);
  } catch (e) {
    $("trn-status").textContent = "Falha: " + e.message;
  }
  $("btn-trn-start").disabled = false;
});

function mostrarTreino(j) {
  const box = $("trn-job");
  if (!j) { box.hidden = true; return; }
  box.hidden = false;
  $("trn-job-msg").textContent = j.msg || j.status;
  let pct = 0;
  if (j.status === "ok") {
    pct = 100;
  } else if (j.status === "erro") {
    pct = j.total ? Math.round(100 * (j.feito || 0) / j.total) : 0;
  } else if (j.total) {
    pct = Math.min(99, Math.round(100 * (j.feito || 0) / j.total));
  }
  $("trn-job-cheia").style.width = `${pct}%`;
  const m = j.metricas || {};
  const partes = [];
  if (j.status === "rodando" && j.total) {
    partes.push(`progresso ${j.feito || 0}/${j.total}`);
  }
  if (j.status === "ok" && j.epochs_rodadas != null) {
    partes.push(`${j.epochs_rodadas} época(s) rodada(s)` + (j.early_stop ? " · early stop" : ""));
  }
  if (m.map50 != null) {
    partes.push(`mAP50 ${fmtNum(m.map50)} · mAP50-95 ${fmtNum(m.map50_95)} · melhor época ${m.epoca_melhor ?? "—"}`);
  }
  $("trn-job-met").textContent = partes.join(" · ");
}

async function acompanharTreino(jid) {
  for (;;) {
    let j;
    try { j = await api("GET", `/api/treino/${jid}`); }
    catch (e) { $("trn-status").textContent = e.message; mostrarTreino(null); return; }
    mostrarTreino(j);
    if (j.status === "ok") {
      $("trn-status").textContent =
        `Concluído · best em ${j.best || "—"} · mAP50 ${fmtNum(j.metricas?.map50)}` +
        (j.early_stop ? ` · parou na época ${j.epochs_rodadas}/${j.epochs}` : "");
      return j;
    }
    if (j.status === "erro") {
      $("trn-status").textContent = "Treino falhou: " + (j.erro || "");
      return j;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function carregarExperimentos() {
  $("exp-status").textContent = "Carregando…";
  $("exp-detalhe").hidden = true;
  try {
    const r = await api("GET", "/api/modelos");
    const exps = r.experimentos || [];
    if (!exps.length) {
      $("lista-exps").innerHTML = `<li class="exp-meta">Nenhum experimento em runs/ ainda.</li>`;
      $("exp-status").textContent = "";
      return;
    }
    $("lista-exps").innerHTML = exps.map((e) => {
      const m = e.metricas || {};
      return `<li data-id="${esc(e.id)}">` +
        `<div><strong>${esc(e.id)}</strong>` +
        `<div class="exp-meta">${esc(e.criado_em)} · mAP50 ${fmtNum(m.map50)} · mAP50-95 ${fmtNum(m.map50_95)} · ` +
        `${m.epochs_feitas ?? "?"} épocas</div></div>` +
        `<button type="button" data-ver="${esc(e.id)}">Detalhes</button></li>`;
    }).join("");
    $("exp-status").textContent = `${exps.length} experimento(s)`;
  } catch (e) {
    $("exp-status").textContent = e.message;
  }
}

$("lista-exps").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-ver]") || ev.target.closest("li[data-id]");
  if (!btn) return;
  const id = btn.dataset.ver || btn.dataset.id;
  $("lista-exps").querySelectorAll("li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
  try {
    const d = await api("GET", `/api/modelos/${encodeURIComponent(id)}`);
    $("exp-detalhe").hidden = false;
    $("exp-titulo").textContent = d.id;
    const m = d.metricas || {};
    $("exp-metricas").innerHTML = [
      ["mAP50", m.map50], ["mAP50-95", m.map50_95],
      ["Precisão", m.precisao], ["Recall", m.recall],
    ].map(([k, v]) => `<div class="met-card"><div class="k">${k}</div><div class="v">${fmtNum(v)}</div></div>`).join("");
    const curva = d.curva || [];
    if (curva.length) {
      const ultimo = curva[curva.length - 1];
      $("exp-curva").textContent =
        `${curva.length} épocas registradas · última mAP50 ${fmtNum(ultimo.map50)}` +
        (m.epoca_melhor != null ? ` · pico na época ${m.epoca_melhor}` : "");
    } else {
      $("exp-curva").textContent = "Sem results.csv ainda.";
    }
    $("exp-plots").innerHTML = (d.plots || []).slice(0, 8).map((p) =>
      `<img src="/api/modelos/${encodeURIComponent(id)}/plot/${encodeURIComponent(p)}" alt="${esc(p)}" title="${esc(p)}">`
    ).join("");
  } catch (e) {
    $("exp-status").textContent = e.message;
  }
});

async function preencherTesteModelos() {
  try {
    const r = await api("GET", "/api/modelos");
    const opts = [];
    for (const b of r.bases || []) opts.push(`<option value="${esc(b.id)}">${esc(b.nome)}</option>`);
    for (const e of r.experimentos || []) {
      if (e.best) opts.push(`<option value="${esc(e.id)}">${esc(e.id)} (best)</option>`);
    }
    $("tst-modelo").innerHTML = opts.join("") || `<option value="">Nenhum modelo</option>`;
    $("tst-stems").innerHTML = S.itens.slice(0, 2000).map((i) =>
      `<option value="${esc(i.stem)}">`).join("");
  } catch (e) {
    $("tst-status").textContent = e.message;
  }
}

$("tst-conf").addEventListener("input", () => {
  $("tst-conf-val").textContent = fmtConf(+$("tst-conf").value / 100);
});

$("btn-tst-atual").addEventListener("click", () => {
  if (S.stem) $("tst-stem").value = S.stem;
});

function desenharTeste() {
  const cnv = $("tst-tela");
  const ctx = cnv.getContext("2d");
  const W = cnv.width, H = cnv.height;
  ctx.fillStyle = "#111";
  ctx.fillRect(0, 0, W, H);
  if (!TST.img) return;
  const iw = TST.img.naturalWidth, ih = TST.img.naturalHeight;
  const e = Math.min(W / iw, H / ih);
  const ox = (W - iw * e) / 2, oy = (H - ih * e) / 2;
  ctx.drawImage(TST.img, ox, oy, iw * e, ih * e);
  for (const b of TST.caixas) {
    const x1 = ox + (b.x - b.w / 2) * iw * e;
    const y1 = oy + (b.y - b.h / 2) * ih * e;
    const ww = b.w * iw * e, hh = b.h * ih * e;
    const cor = CORES_TST[b.c % CORES_TST.length];
    ctx.strokeStyle = cor;
    ctx.lineWidth = 2;
    ctx.strokeRect(x1, y1, ww, hh);
    const txt = `${b.nome || "?"} ${b.conf != null ? b.conf.toFixed(2) : ""}`;
    ctx.font = "12px system-ui";
    const tw = ctx.measureText(txt).width + 6;
    ctx.fillStyle = cor;
    ctx.fillRect(x1, Math.max(0, y1 - 16), tw, 16);
    ctx.fillStyle = "#fff";
    ctx.fillText(txt, x1 + 3, Math.max(12, y1 - 4));
  }
}

async function rodarTeste() {
  const mid = $("tst-modelo").value;
  if (!mid) { $("tst-status").textContent = "Escolha um modelo."; return; }
  $("btn-tst-run").disabled = true;
  $("tst-status").textContent = "Detectando…";
  let r;
  try {
    const arquivo = $("tst-file").files[0];
    if (arquivo) {
      const fd = new FormData();
      fd.append("modelo", mid);
      fd.append("conf", String(+$("tst-conf").value / 100));
      fd.append("imgsz", "640");
      fd.append("file", arquivo);
      const resp = await fetch("/api/testar-arquivo", { method: "POST", body: fd });
      if (!resp.ok) throw new Error(await resp.text());
      r = await resp.json();
      TST.img = await new Promise((res, rej) => {
        const im = new Image();
        im.onload = () => res(im);
        im.onerror = () => rej(new Error("falha ao carregar upload"));
        im.src = URL.createObjectURL(arquivo);
      });
    } else {
      const stem = $("tst-stem").value.trim() || S.stem;
      if (!stem) throw new Error("Informe um stem ou envie um arquivo.");
      r = await api("POST", "/api/testar", {
        modelo: mid, stem, conf: +$("tst-conf").value / 100, imgsz: 640,
      });
      TST.img = await new Promise((res, rej) => {
        const im = new Image();
        im.onload = () => res(im);
        im.onerror = () => rej(new Error("falha ao carregar imagem"));
        im.src = `/img/${encodeURIComponent(stem)}`;
      });
    }
  } catch (e) {
    $("tst-status").textContent = "Falha: " + e.message;
    $("btn-tst-run").disabled = false;
    return;
  }
  TST.caixas = r.caixas || [];
  TST.classes = r.classes || [];
  $("tst-caixas").innerHTML = TST.caixas.map((b) =>
    `<li><span class="cor" style="background:${CORES_TST[b.c % CORES_TST.length]}"></span>` +
    `${esc(b.nome)} <span class="info">${b.conf?.toFixed(2) ?? ""}</span></li>`
  ).join("") || `<li class="hint">Nenhuma detecção</li>`;
  $("tst-status").textContent = `${TST.caixas.length} caixa(s)`;
  desenharTeste();
  $("btn-tst-run").disabled = false;
}
$("btn-tst-run").addEventListener("click", rodarTeste);

// ---------------------------------------------------------------- início

async function iniciar() {
  const cfg = await api("GET", "/api/config");
  S.classes = cfg.classes;
  S.temConf = !!cfg.tem_conf;
  S.modeloOk = !!cfg.modelo_ok;
  S.modeloPath = cfg.modelo || "";
  S.limiarClasse = S.classes.map(() => null);
  carregarLimiar();
  S.itens = await api("GET", "/api/imagens");
  S.itens.forEach((it) => S.porStem.set(it.stem, it));
  preencherFiltros();
  ligarFiltros();
  montarPaleta();
  montarLimiar();
  aplicarFiltros();
  atualizarProgresso();
  new ResizeObserver(redimensionarTela).observe(tela);
  if (S.filtrados.length) abrir(0); else limpar();
}

iniciar().catch((e) => alert("Falha ao iniciar o revisor: " + e.message));
