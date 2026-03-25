/**
 * SurvivorOS Chrome — injected into Open WebUI via nginx sub_filter.
 * Creates the top nav, disclaimer bar, and first-visit acceptance gate.
 *
 * Disclaimer copy: v1.0 (2026-03-24) — attorney-reviewed.
 * Do not change disclaimer text without new legal sign-off.
 */
(function () {
  // ── Styles ──────────────────────────────────────────────────────────
  var style = document.createElement('style');
  style.textContent = [
    /* Top nav */
    '#survivoros-topnav{position:fixed;top:0;left:0;right:0;z-index:99998;height:44px;',
    'background:#1C1A17;display:flex;align-items:center;padding:0 16px;gap:4px;',
    'border-bottom:2px solid #D4570A;font-family:system-ui,sans-serif;}',
    '#survivoros-topnav .so-brand{color:#D4570A;font-weight:700;font-size:14px;',
    'letter-spacing:.05em;flex:none;margin-right:16px;}',
    '#survivoros-topnav a{color:#F7F5F0;font-size:13px;font-weight:500;',
    'text-decoration:none;padding:6px 12px;border-radius:4px;}',
    '#survivoros-topnav a:hover{background:rgba(247,245,240,.12);}',
    /* Disclaimer bar — sits directly under the nav */
    '#survivoros-disclaimer{position:fixed;top:44px;left:0;right:0;z-index:99997;',
    'height:52px;background:#111;color:#fff;padding:0 14px;',
    'display:flex;align-items:center;gap:6px;',
    'font-family:system-ui,sans-serif;font-size:14px;line-height:1.4;',
    'border-bottom:3px solid #c00;overflow:hidden;}',
    '#survivoros-disclaimer a{color:#7bc8ff;white-space:nowrap;}',
    /* App layout fix — push Open WebUI below both fixed bars */
    '.app>div{height:calc(100vh - 96px)!important;',
    'max-height:calc(100dvh - 96px)!important;margin-top:96px!important;}',
    '#chat-container{height:100%!important;max-height:100%!important;}',
    /* First-visit gate */
    '#survivoros-gate{position:fixed;inset:0;z-index:999999;',
    'background:rgba(0,0,0,.85);display:flex;align-items:center;',
    'justify-content:center;font-family:system-ui,sans-serif;}',
    '#survivoros-gate.so-hidden{display:none;}',
    '#survivoros-gate-card{background:#1C1A17;border:1px solid #333;',
    'border-top:4px solid #c00;border-radius:10px;padding:32px;',
    'max-width:520px;width:calc(100% - 32px);color:#F7F5F0;}',
    '#survivoros-gate-card h2{color:#ff6666;font-size:18px;font-weight:700;',
    'margin:0 0 16px;letter-spacing:.02em;}',
    '#survivoros-gate-card p{font-size:15px;line-height:1.6;',
    'color:rgba(247,245,240,.8);margin:0 0 12px;}',
    '#survivoros-gate-card .so-fine{font-size:13px;color:rgba(247,245,240,.5);}',
    '#survivoros-gate-accept{display:block;width:100%;margin-top:24px;padding:14px;',
    'background:#D4570A;color:#fff;border:none;border-radius:6px;',
    'font-size:15px;font-weight:600;cursor:pointer;font-family:system-ui,sans-serif;}',
    '#survivoros-gate-accept:hover{background:#b34208;}',
  ].join('');
  document.head.appendChild(style);

  // ── Top nav ─────────────────────────────────────────────────────────
  var nav = document.createElement('nav');
  nav.id = 'survivoros-topnav';
  nav.setAttribute('aria-label', 'SurvivorOS navigation');
  nav.innerHTML = [
    '<span class="so-brand">SurvivorOS</span>',
    '<a href="/">Chat</a>',
    '<a href="/library">Library</a>',
    '<a href="/admin">Admin</a>',
  ].join('');
  document.body.appendChild(nav);

  // ── Disclaimer bar ──────────────────────────────────────────────────
  // Attorney-reviewed copy v1.0 (2026-03-24). Do not edit without legal sign-off.
  var disc = document.createElement('div');
  disc.id = 'survivoros-disclaimer';
  disc.setAttribute('role', 'note');
  disc.innerHTML = [
    '<strong style="color:#ff6666;white-space:nowrap;">&#9888; NOT MEDICAL ADVICE.</strong>',
    '&nbsp;AI can be wrong or dangerous. Verify anything critical.',
    '&nbsp;Emergency? <strong>Call 911.</strong>',
    '&nbsp;<a href="/library">Offline Library &rarr;</a>',
  ].join('');
  document.body.appendChild(disc);

  // ── First-visit acceptance gate ─────────────────────────────────────
  // Attorney-reviewed copy v1.0 (2026-03-24). Do not edit without legal sign-off.
  var gate = document.createElement('div');
  gate.id = 'survivoros-gate';
  gate.setAttribute('role', 'dialog');
  gate.setAttribute('aria-modal', 'true');
  gate.setAttribute('aria-labelledby', 'survivoros-gate-title');
  gate.innerHTML = [
    '<div id="survivoros-gate-card">',
    '  <h2 id="survivoros-gate-title">&#9888; Before you continue</h2>',
    '  <p><strong style="color:#ff6666;">',
    '    This is not medical, safety, legal, or professional advice of any kind.',
    '  </strong></p>',
    '  <p>SurvivorOS is a general reference tool. AI can produce incorrect,',
    '    incomplete, or dangerous information — including on medical emergencies,',
    '    fire behavior, and survival topics — without warning.</p>',
    '  <p>Do not rely on AI output for any life-safety decision.',
    '    In an emergency, <strong>call 911 first</strong>.',
    '    Always verify with a qualified professional before acting.</p>',
    '  <p class="so-fine">By tapping "I Accept" you acknowledge this tool is for',
    '    reference only and accept sole responsibility for all decisions made.</p>',
    '  <button id="survivoros-gate-accept">I Accept — Continue to SurvivorOS</button>',
    '</div>',
  ].join('');
  document.body.appendChild(gate);

  // Hide gate if already accepted; otherwise wire up the button.
  if (localStorage.getItem('survivoros-accepted')) {
    gate.classList.add('so-hidden');
  } else {
    document.getElementById('survivoros-gate-accept').addEventListener('click', function () {
      localStorage.setItem('survivoros-accepted', '1');
      gate.classList.add('so-hidden');
    });
  }
})();
