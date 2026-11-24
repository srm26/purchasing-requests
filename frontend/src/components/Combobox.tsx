import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { Option } from "../api";

/** Searchable select for long lists (job numbers, cost centers). Type to filter by code or description. */
export default function Combobox({ id, options, value, onChange, placeholder, invalid }: {
  id?: string; options: Option[]; value: string; onChange: (code: string) => void; placeholder?: string; invalid?: boolean;
}) {
  const listId = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const selected = options.find((o) => o.code === value);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? options.filter((o) => `${o.code} ${o.label}`.toLowerCase().includes(q)) : options;
    return list.slice(0, 100);
  }, [options, query]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const pick = (o: Option) => { onChange(o.code); setQuery(""); setOpen(false); };
  // Options without a description (e.g. "Not in the list") show just the code.
  const shown = selected ? (selected.label ? `${selected.code} — ${selected.label}` : selected.code) : "";
  const display = open ? query : shown;

  return (
    <div ref={wrap} style={{ position: "relative" }}>
      <input
        id={id} className="input" role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
        aria-invalid={invalid || undefined} autoComplete="off" placeholder={selected && open ? shown : placeholder}
        value={display} style={{ paddingRight: 34 }}
        onFocus={() => { setOpen(true); setActive(0); }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && open && filtered[active]) { e.preventDefault(); pick(filtered[active]); }
          else if (e.key === "Escape" || e.key === "Tab") setOpen(false);
        }}
      />
      <ChevronDown size={16} aria-hidden style={{ position: "absolute", right: 12, top: 11, color: "var(--text-3)", pointerEvents: "none" }} />
      {open && (
        <ul id={listId} role="listbox" className="combo-list">
          {filtered.length === 0 && <li className="combo-empty">No matches</li>}
          {filtered.map((o, i) => (
            <li key={o.code} role="option" aria-selected={o.code === value}
              className={`${i === active ? "active" : ""} ${o.code === value ? "selected" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); pick(o); }} onMouseEnter={() => setActive(i)}>
              <span className="mono">{o.code}</span><span>{o.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
