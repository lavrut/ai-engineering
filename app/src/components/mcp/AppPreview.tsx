import { useEffect, useRef, useState } from 'preact/hooks';
import {
  APP_SANDBOX,
  describeAppMessage,
  hostReply,
  isAppMessage,
  toolInputMessage,
  type AppPreviewProps,
} from '../../lib/mcpAids';
import { BASE_CSS } from './mcpStyle';

export default function AppPreview({ uri, mimeType, doc, bytes, description, sample }: AppPreviewProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [log, setLog] = useState<string[]>([]);

  // The frame has an opaque origin (no allow-same-origin), so messages go to and come from "*";
  // the source check is what ties them to this frame.
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || e.source !== win || !isAppMessage(e.data)) return;
      setLog((l) => [...l, describeAppMessage(e.data)]);
      const reply = hostReply(e.data);
      if (reply) win.postMessage(reply, '*');
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const send = () => {
    frame.current?.contentWindow?.postMessage(toolInputMessage(sample), '*');
    setLog((l) => [...l, `The preview host sent the tool input: ${sample.name} for ${sample.id} with label ${sample.label}.`]);
  };

  return (
    <section class="mc" aria-label="App preview">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {uri} · {mimeType}
        </p>
        <p class="mc-label">Sandboxed · no network · nothing is written</p>
      </header>

      <p id="ap-desc" class="mc-hint">
        {description}
      </p>

      <div class="ap-frame">
        <iframe
          ref={frame}
          title="Label a ticket App (sandboxed preview)"
          sandbox={APP_SANDBOX}
          srcdoc={doc}
          referrerpolicy="no-referrer"
          aria-describedby="ap-desc"
        />
      </div>

      <div class="mc-buttons">
        <button type="button" class="mc-btn mc-btn-primary" onClick={send}>
          Send the tool input ({sample.id}, {sample.label})
        </button>
        <button type="button" class="mc-btn" onClick={() => setLog([])} disabled={log.length === 0}>
          Clear log
        </button>
      </div>
      <p class="mc-hint">
        The page is {bytes.toLocaleString('en-US')} bytes of HTML, CSS and script, with no build step. It runs in an iframe with{' '}
        <code>sandbox=&quot;{APP_SANDBOX}&quot;</code>: it can run script but has no access to this site, and a policy blocks every
        connection. This page plays the host: it answers the App&apos;s hello and refuses its tool call, because no server is behind it.
      </p>
      <div class="ap-log" role="log" aria-live="polite" aria-label="Messages between the App and the host">
        {log.length === 0 ? <p class="mc-hint">No messages yet.</p> : <ol>{log.map((l, i) => <li key={i}>{l}</li>)}</ol>}
      </div>
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.ap-frame { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; background: var(--surface); }
.ap-frame iframe { display: block; width: 100%; height: 22rem; border: 0; }
.ap-log { min-width: 0; padding: 8px 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); font-size: var(--text-small); color: var(--ink); }
.ap-log ol { margin: 0; padding-left: 1.25rem; display: grid; gap: 4px; overflow-wrap: anywhere; }
.ap-log p { margin: 0; }
.mc code { font-family: var(--font-mono); font-size: 0.875em; }
`;
