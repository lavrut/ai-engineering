import { useId } from 'preact/hooks';

/** A labelled select for the agents aids: the label names only the choice, so a screen reader (and a
 *  test) finds it by "Ticket" or "Engine", not by the option texts. */
export default function Picker<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const id = useId();
  return (
    <div class="ag-pick">
      <label for={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange((e.target as HTMLSelectElement).value as T)}>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
