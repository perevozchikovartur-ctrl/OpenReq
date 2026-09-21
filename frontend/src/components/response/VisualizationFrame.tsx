import { useMemo } from "react";

interface VisualizationFrameProps {
  template: string;
  data: unknown;
}

type RecordValue = Record<string, unknown>;

function isRecord(value: unknown): value is RecordValue {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function valueAt(path: string, scope: unknown, root: unknown): unknown {
  const trimmed = path.trim();
  if (!trimmed || trimmed === "this") return scope;
  const parts = trimmed.split(".");
  const firstPart = parts[0];
  if (!firstPart) return undefined;
  let value: unknown = isRecord(scope) && firstPart in scope ? scope : root;
  for (const part of parts) {
    if (part === "length" && (Array.isArray(value) || typeof value === "string")) {
      value = value.length;
    } else if (isRecord(value)) {
      value = value[part];
    } else {
      return undefined;
    }
  }
  return value;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char] ?? char);
}

/** A deliberately small, safe subset of Handlebars used by Postman visualizers. */
function renderTemplate(template: string, data: unknown, scope: unknown = data): string {
  let output = template;
  const block = /\{\{#(each|if)\s+([^}]+)\}\}([\s\S]*?)\{\{\/\1\}\}/g;

  // Re-run to process blocks that became reachable after an outer block expands.
  let previous: string;
  do {
    previous = output;
    output = output.replace(block, (_match, kind: string, path: string, body: string) => {
      const value = valueAt(path, scope, data);
      if (kind === "if") {
        const [truthy = "", falsy = ""] = body.split(/\{\{else\}\}/, 2);
        return value ? renderTemplate(truthy, data, scope) : renderTemplate(falsy, data, scope);
      }
      if (!Array.isArray(value)) return "";
      return value.map((item) => renderTemplate(body, data, item)).join("");
    });
  } while (output !== previous);

  return output.replace(/\{\{\s*([^{}#/]\S*?)\s*\}\}/g, (_match, path: string) =>
    escapeHtml(valueAt(path, scope, data)),
  );
}

export default function VisualizationFrame({ template, data }: VisualizationFrameProps) {
  const srcDoc = useMemo(() => {
    const rendered = renderTemplate(template, data);
    const csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:\">";
    return `<!doctype html><html><head><meta charset=\"utf-8\">${csp}</head><body>${rendered}</body></html>`;
  }, [template, data]);

  return (
    <iframe
      srcDoc={srcDoc}
      sandbox=""
      title="Response visualization"
      style={{ width: "100%", height: "100%", minHeight: 360, border: "none", background: "#fff" }}
    />
  );
}
