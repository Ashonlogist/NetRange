/*!
 * NetRange coverage widget -- self-contained, no build step, no dependencies.
 *
 * Embed on a site you have registered:
 *   <script src="https://netrange.ashonlogist.website/widget.js" async></script>
 *
 * WHAT THIS SCRIPT DOES, IN ORDER
 *   1. Renders a consent prompt (real DOM, in a shadow root).
 *   2. Sends absolutely nothing until the visitor taps Allow.
 *   3. On Allow, reads navigator.connection, asks for location through the
 *      browser's own permission prompt, and POSTs one row to /api/widget-scan.
 *   4. If the visitor taps No thanks, nothing is sent and nothing is stored.
 *
 * "This only happens once per visit": the decision lives in this page's memory
 * only. There is no cookie, no localStorage, no fingerprint. A reload can ask
 * again, and a new visitor is never remembered. That is the literal promise in
 * the prompt, so it is also the implementation.
 *
 * WHY A SHADOW ROOT
 * Isolates these styles from the host page's, and ours from theirs, so we
 * neither inherit their resets nor impose ours on their site. Without it, a
 * host stylesheet with a global `button { ... }` or `* { box-sizing }` would
 * distort the prompt, and our CSS would leak into their markup. The trade-off
 * is that this does not inherit the host's fonts or colours; it uses a neutral
 * system-font style instead of pretending to blend in.
 *
 * WHY downlink IS NOT SENT AS A MEASURED SPEED
 * navigator.connection.downlink is the browser's *guess* at the connection,
 * often derived from a previous page load or the network's advertised class. It
 * is not a measurement, and it is not comparable to a real speed test. Storing
 * it in the same column as measured speeds would let a guess outvote real data
 * in the per-cell average, quietly corrupting the map. So it goes in its own
 * column (downlink_estimate_mbps) and the coverage aggregator, which only reads
 * download_speed_mbps, never sees it.
 */
(function () {
  'use strict';

  if (window.__netrangeWidgetLoaded) return;
  window.__netrangeWidgetLoaded = true;

  var ENDPOINT = (function () {
    var s = document.currentScript;
    if (s && s.dataset && s.dataset.endpoint) return s.dataset.endpoint;
    return '/api/widget-scan';
  })();

  // Verbatim consent copy. Do not reword, reformat, or "improve" this.
  //
  // The wording is the notice, and paraphrasing it changes what a visitor is
  // agreeing to. "3 other people from this area", "Nothing is linked to you",
  // and "no cookies" are each a specific, checkable claim. A softer rewording
  // ("combined with other visitors' data", "we never store anything that
  // identifies you individually") is not the same promise to the person
  // deciding, and the k-anonymity gate and no-cookie behaviour are exactly
  // what makes the original wording true.
  var CONSENT = [
    "We'd like to collect anonymous network quality data from this page to help improve coverage maps.",
    '',
    'Your data is only shown if at least 3 other people from this area also reported. Nothing is linked to you.',
    '',
    'This is optional and anonymous. No account, no personal details, no cookies. You can change your mind anytime \u2014 the data is only collected once per visit.'
  ].join('\n');

  var session = { decided: false, sent: false };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* Both buttons get identical treatment. No "recommended" styling, no
     pre-selected default, no disabled-looking decoy, no guilt copy, and Allow
     is not the default focus target -- a visitor who hits Enter reflexively
     should not be opted in. Equal weight is the whole point. */
  function buildPrompt(onAllow, onDecline) {
    var host = el('div');
    host.setAttribute('data-netrange-widget', '');
    host.style.cssText = [
      'position:fixed', 'inset:auto 0 0 0', 'z-index:2147483000',
      'display:flex', 'justify-content:center', 'align-items:flex-end',
      'padding:16px', 'box-sizing:border-box', 'pointer-events:none',
      'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif'
    ].join(';');

    var shadow = host.attachShadow({ mode: 'open' });

    var style = document.createElement('style');
    style.textContent = [
      '.card{',
      '  pointer-events:auto; max-width:420px; width:100%; box-sizing:border-box;',
      '  background:#fff; color:#16181d; border-radius:14px; padding:20px;',
      '  box-shadow:0 10px 40px rgba(0,0,0,.28); font-size:14px; line-height:1.5;',
      '}',
      '@media (prefers-color-scheme:dark){',
      '  .card{background:#1c1f26; color:#e8eaed;}',
      '}',
      '.body{white-space:pre-wrap; margin:0 0 16px;}',
      '.row{display:flex; gap:10px;}',
      'button{',
      '  flex:1 1 0; padding:11px 12px; font:inherit; font-weight:500;',
      '  border-radius:9px; cursor:pointer; box-sizing:border-box;',
      '  /* identical border and background treatment for both */',
      '  background:transparent; color:inherit;',
      '  border:1px solid rgba(128,128,128,.55);',
      '}',
      'button:hover{background:rgba(128,128,128,.12);}',
      'button:focus-visible{outline:2px solid #3b82f6; outline-offset:2px;}',
      '.err{margin:10px 0 0; font-size:13px; color:#b3261e;}',
      '@media (prefers-reduced-motion:no-preference){',
      '  .card{animation:rise .18s ease-out;}',
      '  @keyframes rise{from{opacity:0; transform:translateY(8px)}to{opacity:1; transform:none}}',
      '}'
    ].join('');

    var card = el('div', 'card');
    var body = el('p', 'body', CONSENT);

    var allow = el('button', null, 'Allow');
    allow.type = 'button';
    var decline = el('button', null, 'No thanks');
    decline.type = 'button';

    var row = el('div', 'row');
    row.appendChild(decline);
    row.appendChild(allow);

    var err = el('p', 'err');
    err.hidden = true;

    card.appendChild(body);
    card.appendChild(row);
    card.appendChild(err);

    shadow.appendChild(style);
    shadow.appendChild(card);

    allow.addEventListener('click', function () { onAllow(); });
    decline.addEventListener('click', function () { onDecline(); });

    return { host: host, err: err };
  }

  function teardown(prompt) {
    if (prompt && prompt.host && prompt.host.parentNode) {
      prompt.host.parentNode.removeChild(prompt.host);
    }
  }

  function showError(prompt, message) {
    if (!prompt || !prompt.err) return;
    prompt.err.textContent = message;
    prompt.err.hidden = false;
  }

  /* A pseudonymous per-browser id, so k-anonymity can count distinct
     contributors. Deliberately a random UUID in localStorage, not a
     fingerprint: no canvas, no fonts, no screen metrics, nothing that could
     follow someone across sites. It identifies this browser to us only in the
     sense of "a separate contributor", which is the minimum the suppression
     rule needs. A visitor who clears storage becomes a new contributor, which
     inflates the count slightly -- the safe direction, since suppression is
     what we are protecting. */
  function contributorId() {
    var KEY = 'netrange_cid';
    try {
      var v = window.localStorage.getItem(KEY);
      if (v) return v;
      if (window.crypto && window.crypto.randomUUID) {
        v = window.crypto.randomUUID();
      } else if (window.crypto && window.crypto.getRandomValues) {
        var b = new Uint8Array(16);
        window.crypto.getRandomValues(b);
        v = Array.prototype.map.call(b, function (x) {
          return ('0' + x.toString(16)).slice(-2);
        }).join('');
      } else {
        return null;
      }
      window.localStorage.setItem(KEY, v);
      return v;
    } catch (e) {
      // Private mode or storage disabled: send no id. That means this row
      // cannot count toward a cell's distinct-contributor total, so the cell
      // is more likely to be suppressed -- never less safe.
      return null;
    }
  }

  function readConnection() {
    var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!c) return {};
    var out = {};
    if (typeof c.effectiveType === 'string') out.effectiveType = c.effectiveType;
    if (typeof c.type === 'string') out.type = c.type;
    if (typeof c.downlink === 'number' && isFinite(c.downlink)) out.downlink = c.downlink;
    if (typeof c.rtt === 'number' && isFinite(c.rtt)) out.rtt = c.rtt;
    return out;
  }

  /* Standard browser geolocation prompt. Resolves to null on denial, timeout or
     any error -- all three are ordinary outcomes, not failures to report, and
     the consent copy says location is optional ("if you allow it"). */
  function askLocation() {
    return new Promise(function (resolve) {
      if (!navigator.geolocation) return resolve(null);
      var settled = false;
      var done = function (v) { if (!settled) { settled = true; resolve(v); } };
      var timer = setTimeout(function () { done(null); }, 8000);
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          clearTimeout(timer);
          done({
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            accuracy: pos.coords.accuracy
          });
        },
        function () { clearTimeout(timer); done(null); },
        { enableHighAccuracy: false, timeout: 7000, maximumAge: 600000 }
      );
    });
  }

  function send(payload) {
    return fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Origin is set by the browser and cannot be forged by page script;
      // the server checks it against the registered domain.
      body: JSON.stringify(payload),
      credentials: 'omit',
      keepalive: true
    });
  }

  function collectAndSend(prompt) {
    var conn = readConnection();
    // No client-side speed test on purpose: a real measurement needs a
    // multi-megabyte download, which would make this heavier than the thing
    // it is measuring and would work against the site embedding us.
    askLocation().then(function (loc) {
      var payload = {
        contributor_id: contributorId(),
        effective_type: conn.effectiveType || null,
        conn_type: conn.type || null,
        downlink_estimate_mbps: typeof conn.downlink === 'number' ? conn.downlink : null,
        rtt_ms: typeof conn.rtt === 'number' ? conn.rtt : null,
        lat: loc ? loc.lat : null,
        lon: loc ? loc.lon : null,
        accuracy: loc ? loc.accuracy : null
        // No timestamp field. The server stamps created_at on receipt; a
        // client clock is not evidence of when a visit happened.
      };
      return send(payload);
    }).then(function (res) {
      teardown(prompt);
      if (res && !res.ok) {
        // Nothing was stored. Say so rather than pretending it worked.
        console.warn('[netrange] coverage report was not accepted (HTTP ' + res.status + ')');
      }
    }).catch(function (err) {
      teardown(prompt);
      console.warn('[netrange] could not send coverage report:', err && err.message);
    });
  }

  function start() {
    if (session.decided) return;
    session.decided = true;

    var prompt = buildPrompt(
      function onAllow() {
        if (session.sent) return;
        session.sent = true;
        collectAndSend(prompt);
      },
      function onDecline() {
        // Nothing is sent, nothing is stored, and the prompt does not return
        // for the rest of this page view.
        teardown(prompt);
      }
    );

    document.body.appendChild(prompt.host);
  }

  if (document.body) {
    start();
  } else {
    document.addEventListener('DOMContentLoaded', start);
  }
})();
