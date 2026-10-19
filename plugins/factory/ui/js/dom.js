// Tiny DOM helpers. Text is always set with textContent; attributes are set individually, so data from
// agents and repositories can never become markup. The markdown renderer is the only innerHTML user and it
// escapes everything it emits.

const SVG_NS = "http://www.w3.org/2000/svg";

function append(el, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) { child.forEach((c) => append(el, c)); return; }
  el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
}

function setAttrs(el, attrs, isSvg) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.setAttribute("class", Array.isArray(value) ? value.filter(Boolean).join(" ") : value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "text") el.textContent = value;
    else if (!isSvg && (key === "value" || key === "checked" || key === "selected")) el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs && (attrs instanceof Node || Array.isArray(attrs) || typeof attrs !== "object")) {
    children.unshift(attrs);
  } else {
    setAttrs(el, attrs, false);
  }
  children.forEach((c) => append(el, c));
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  setAttrs(el, attrs, true);
  children.forEach((c) => append(el, c));
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...children) {
  clear(el);
  children.forEach((c) => append(el, c));
  return el;
}

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

let uid = 0;
export function nextId(prefix = "id") {
  uid += 1;
  return `${prefix}-${uid}`;
}

// Re-render a region only when its data changed; keeps focus and scroll stable while polling.
export function patch(el, key, render) {
  if (el.__key === key) return false;
  el.__key = key;
  const scroll = el.scrollTop;
  mount(el, render());
  el.scrollTop = scroll;
  return true;
}
