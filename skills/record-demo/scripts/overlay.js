// Injected into every recorded page. Draws the things a headless browser has
// no native version of: a mouse cursor, click ripples, captions, highlight
// rings and title cards. Lives in a shadow root so site CSS cannot touch it.
(() => {
  if (window.__demo) return;

  const CSS = `
    :host { all: initial; position: fixed; inset: 0; z-index: 2147483647; pointer-events: none; }
    .cursor { position: fixed; left: 0; top: 0; width: 30px; height: 30px; will-change: transform;
              filter: drop-shadow(0 2px 3px rgba(0,0,0,.45)); transition: opacity .2s; }
    .ripple { position: fixed; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
              background: rgba(99,102,241,.45); border: 2px solid rgba(129,140,248,.95);
              animation: ripple .5s ease-out forwards; }
    @keyframes ripple { to { transform: scale(4.2); opacity: 0; } }
    .caption { position: fixed; left: 0; right: 0; bottom: 34px; margin: 0 auto; width: fit-content;
               transform: translateY(12px); opacity: 0; max-width: 84%; padding: 12px 22px; border-radius: 12px; text-align: center;
               font: 600 21px/1.35 Inter, -apple-system, "Segoe UI", system-ui, sans-serif; color: #fff;
               background: rgba(15,15,25,.9); border: 1px solid rgba(255,255,255,.14);
               box-shadow: 0 10px 34px rgba(0,0,0,.5); transition: opacity .3s, transform .3s; }
    .caption.on { opacity: 1; transform: translateY(0); }
    .ring { position: fixed; border-radius: 10px; border: 3px solid #818cf8; opacity: 0;
            box-shadow: 0 0 0 5px rgba(129,140,248,.22), 0 0 26px rgba(129,140,248,.55);
            transition: opacity .3s; }
    .ring.on { opacity: 1; }
    .card { position: fixed; inset: 0; display: flex; flex-direction: column; align-items: center;
            justify-content: center; gap: 16px; padding: 0 10%; text-align: center; opacity: 0;
            background: radial-gradient(120% 120% at 50% 0%, #1e1b4b 0%, #0a0a12 62%);
            font-family: Inter, -apple-system, "Segoe UI", system-ui, sans-serif; color: #fff;
            transition: opacity .45s; }
    .card.on { opacity: 1; }
    .card h1 { margin: 0; font-size: 54px; line-height: 1.12; font-weight: 800; letter-spacing: -.02em; }
    .card p { margin: 0; font-size: 24px; line-height: 1.4; color: #a5b4fc; }
  `;
  const ARROW = `<svg class="cursor" viewBox="0 0 24 24"><path d="M4 2.5v17.2l4.7-4.4 3 6.9 2.9-1.3-3-6.8h6.5z"
                 fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;

  let root, cursor, caption, ring, card;
  const state = { x: -100, y: -100, caption: '', card: null, showCursor: true };

  function mount() {
    if (root && root.host.isConnected) return true;
    if (!document.documentElement) return false;
    const host = document.createElement('demo-recorder-overlay');
    root = host.attachShadow({ mode: 'closed' });
    const sheet = document.createElement('style');
    sheet.textContent = CSS;
    root.appendChild(sheet);
    const wrap = document.createElement('div');
    wrap.innerHTML = `<div class="ring"></div><div class="caption"></div><div class="card"></div>${ARROW}`;
    root.appendChild(wrap);
    ring = root.querySelector('.ring');
    caption = root.querySelector('.caption');
    card = root.querySelector('.card');
    cursor = root.querySelector('.cursor');
    document.documentElement.appendChild(host);
    // A card that was up before a navigation must reappear without fading in again.
    card.style.transition = 'none';
    render();
    requestAnimationFrame(() => { card.style.transition = ''; });
    return true;
  }

  function render() {
    if (!cursor) return;
    cursor.style.transform = `translate(${state.x - 5}px, ${state.y - 3}px)`;
    cursor.style.opacity = state.showCursor ? 1 : 0;
    caption.textContent = state.caption;
    caption.classList.toggle('on', !!state.caption);
    if (state.card) {
      card.textContent = '';
      const h = document.createElement('h1');
      h.textContent = state.card.title || '';
      card.appendChild(h);
      if (state.card.subtitle) {
        const p = document.createElement('p');
        p.textContent = state.card.subtitle;
        card.appendChild(p);
      }
    }
    card.classList.toggle('on', !!state.card);
  }

  const ease = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

  window.__demo = {
    // Called from Python after every step so overlay state survives navigations.
    sync(s) { Object.assign(state, s); mount(); render(); },
    ripple() {
      if (!mount()) return;
      const r = document.createElement('div');
      r.className = 'ripple';
      r.style.left = state.x + 'px';
      r.style.top = state.y + 'px';
      root.appendChild(r);
      setTimeout(() => r.remove(), 600);
    },
    ring(rect) {
      if (!mount()) return;
      if (!rect) { ring.classList.remove('on'); return; }
      const pad = 8;
      Object.assign(ring.style, {
        left: rect.x - pad + 'px', top: rect.y - pad + 'px',
        width: rect.width + pad * 2 + 'px', height: rect.height + pad * 2 + 'px',
      });
      ring.classList.add('on');
    },
    scrollTo(target, ms) {
      const el = document.scrollingElement || document.documentElement;
      const max = el.scrollHeight - window.innerHeight;
      const to = Math.max(0, Math.min(target, max));
      const from = window.scrollY;
      if (Math.abs(to - from) < 1) return Promise.resolve(0);
      const prev = document.documentElement.style.scrollBehavior;
      document.documentElement.style.scrollBehavior = 'auto';
      return new Promise(resolve => {
        const t0 = performance.now();
        const tick = now => {
          const t = Math.min(1, (now - t0) / ms);
          window.scrollTo(0, from + (to - from) * ease(t));
          if (t < 1) return requestAnimationFrame(tick);
          document.documentElement.style.scrollBehavior = prev;
          resolve(to - from);
        };
        requestAnimationFrame(tick);
      });
    },
  };

  document.addEventListener('mousemove', e => { state.x = e.clientX; state.y = e.clientY; render(); }, true);
  document.addEventListener('mousedown', () => window.__demo.ripple(), true);
  if (!mount()) document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
