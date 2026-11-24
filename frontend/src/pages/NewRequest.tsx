import { forwardRef, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Controller, useForm, type Control, type FieldError, type Path } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileText, FileUp, Mail, Send, X } from "lucide-react";
import { getLookups, getRequest, submitRequest, type RequestDetail } from "../api";
import { fileSize, money } from "../format";
import Combobox from "../components/Combobox";
import InfoPopover from "../components/InfoPopover";
import { EmailBadge, ErrorBox, PageLoader, Spinner, useMe, useToast } from "../components/ui";

const MAX_AGREEMENT_FILES = 3;
const MAX_SUPPORTING_FILES = 10;
const MAX_MB = 20;
// Extra choice at the end of the approver list; picking it shows a box to type the approver's email.
const NOT_LISTED = { code: "Not in the list", label: "" };
const HIGH_VALUE_AMOUNT = 250_000; // keep in step with HIGH_VALUE_AMOUNT in backend/app/models.py
const EXTENSIONS = [".pdf", ".doc", ".docx", ".xls", ".xlsx"];
const yesNo = z.boolean({ error: "Choose Yes or No" });
const splitEmails = (s?: string) => (s ?? "").split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
// Local calendar date as YYYY-MM-DD, for the "not in the future" check.
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 10);

const schema = z.object({
  approver_email: z.string().min(1, "Approver is required"),
  other_approver_email: z.string().trim().optional(),
  po_recipients: z.string().optional(),
  is_po_extension: yesNo,
  previous_po: z.string().trim().optional(),
  is_capital_project: yesNo,
  capital_project_number: z.string().trim().optional(),
  capital_cost_center: z.string().trim().optional(),
  project_number: z.string().trim().regex(/^\d{9}$/, "Project number must be exactly 9 digits"),
  cost_center: z.string().trim().regex(/^\d{4}$/, "Task / cost center must be exactly 4 digits"),
  supplier_name: z.string().trim().min(2, "Supplier name is required"),
  start_date: z.string().min(1, "Start date is required"),
  end_date: z.string().min(1, "End date is required"),
  agreement_signed: yesNo,
  signature_date: z.string().optional(),
  amount: z.number({ error: "Enter the total value" }).positive("Value must be more than $0"),
  legal_review: z.boolean().optional(),
  is_urgent: z.boolean(),
  urgent_reason: z.string().optional(),
  description: z.string().trim().optional(),
}).superRefine((v, ctx) => {
  const issue = (path: string, message: string) => ctx.addIssue({ code: "custom", path: [path], message });
  if (v.approver_email === NOT_LISTED.code && !z.email().safeParse(v.other_approver_email).success)
    issue("other_approver_email", "Enter the approver's email address");
  const bad = splitEmails(v.po_recipients).filter((e) => !z.email().safeParse(e).success);
  if (bad.length) issue("po_recipients", `Not a valid email: ${bad.join(", ")}`);
  if (v.is_po_extension && !v.previous_po) issue("previous_po", "Previous PO is required for an extension / add-on");
  if (v.is_capital_project && !/^\d{9}$/.test(v.capital_project_number ?? "")) issue("capital_project_number", "Capital project number must be exactly 9 digits");
  if (v.is_capital_project && !/^\d{4}$/.test(v.capital_cost_center ?? "")) issue("capital_cost_center", "Capital task / cost center must be exactly 4 digits");
  if (v.amount >= HIGH_VALUE_AMOUNT && v.legal_review === undefined) issue("legal_review", "Choose Yes or No");
  if (v.start_date && v.end_date && v.end_date < v.start_date) issue("end_date", "End date can't be before the start date");
  if (v.agreement_signed && !v.signature_date) issue("signature_date", "Date of signature is required");
  if (v.agreement_signed && v.signature_date && v.signature_date > today()) issue("signature_date", "Date of signature can't be in the future");
  if (v.is_urgent && !v.urgent_reason) issue("urgent_reason", "Choose a reason");
});
type FormValues = z.infer<typeof schema>;

const DEFAULTS: Partial<FormValues> = {
  approver_email: "", other_approver_email: "", po_recipients: "", previous_po: "", capital_project_number: "",
  capital_cost_center: "", project_number: "", cost_center: "",
  supplier_name: "", start_date: "", end_date: "", signature_date: "", is_urgent: false, urgent_reason: "", description: "",
};

function Field({ label, error, required, hint, span, htmlFor, children }: {
  label: string; error?: FieldError; required?: boolean; hint?: string; span?: boolean; htmlFor?: string; children: ReactNode;
}) {
  return (
    <div className={`field ${span ? "span-2" : ""} ${error ? "invalid" : ""}`}>
      <label htmlFor={htmlFor}>{label}{required && <span className="req" aria-hidden>*</span>}</label>
      {children}
      {error ? <span className="field-error" role="alert">{error.message}</span> : hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

function Section({ step, title, intro, children }: { step: number; title: string; intro: string; children: ReactNode }) {
  return (
    <div className="form-section">
      <div className="form-section__intro"><h2><span className="step">{step}</span>{title}</h2><p>{intro}</p></div>
      <div className="fields">{children}</div>
    </div>
  );
}

// forwardRef so react-hook-form's register() can reach the checkbox (React 18 drops `ref` from plain props).
const Toggle = forwardRef<HTMLInputElement, { label: string } & React.InputHTMLAttributes<HTMLInputElement>>(
  ({ label, ...rest }, ref) => (
    <label className="toggle-row">
      <span>{label}</span>
      <span className="switch"><input type="checkbox" ref={ref} {...rest} /><i /></span>
    </label>
  ),
);

/** Required Yes/No: starts unanswered so the requester has to choose. */
function YesNo({ control, name, label }: { control: Control<FormValues>; name: Path<FormValues>; label: string }) {
  return (
    <Controller control={control} name={name} render={({ field }) => (
      <div className="segmented" role="group" aria-label={label}>
        {([["Yes", true], ["No", false]] as const).map(([text, v]) => (
          <button key={text} type="button" aria-pressed={field.value === v} onClick={() => { field.onChange(v); field.onBlur(); }}>{text}</button>
        ))}
      </div>
    )} />
  );
}

/** One upload area (drag and drop or click). Checks type, size, duplicates and the per-section limit. */
function FileDrop({ id, label, required, hint, max, files, onChange, error }: {
  id: string; label: string; required: boolean; hint: string; max: number; files: File[]; onChange: (f: File[]) => void; error?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [problem, setProblem] = useState("");

  function add(list: FileList | null) {
    if (!list) return;
    setProblem("");
    const next = [...files];
    for (const f of Array.from(list)) {
      if (!EXTENSIONS.some((x) => f.name.toLowerCase().endsWith(x))) { setProblem(`"${f.name}" isn't a PDF, Word or Excel file.`); continue; }
      if (f.size > MAX_MB * 1024 * 1024) { setProblem(`"${f.name}" is over ${MAX_MB} MB.`); continue; }
      if (next.some((x) => x.name === f.name && x.size === f.size)) continue;
      if (next.length >= max) { setProblem(`You can attach up to ${max} files here.`); break; }
      next.push(f);
    }
    onChange(next);
  }
  const shown = problem || error;

  return (
    <div className={`field span-2 ${shown ? "invalid" : ""}`}>
      <span className="label" id={`${id}-label`}>{label}{required && <span className="req" aria-hidden>*</span>}</span>
      <div
        className={`drop ${drag ? "drag" : ""}`} role="button" tabIndex={0} aria-labelledby={`${id}-label`}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
      >
        <div className="drop__icon"><FileUp size={22} aria-hidden /></div>
        <div><strong>Click to choose files</strong> or drag them here</div>
        <div className="small muted">{hint} · PDF, Word or Excel (incl. .doc/.xls) · up to {max} files, {MAX_MB} MB each</div>
        <input ref={input} type="file" accept={EXTENSIONS.join(",")} multiple hidden
          onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </div>
      {shown && <span className="field-error" role="alert">{shown}</span>}
      {files.length > 0 && (
        <ul className="files">
          {files.map((f) => (
            <li key={f.name + f.size} className="file">
              <FileText size={20} className="file__icon" aria-hidden />
              <span className="file__name">{f.name}</span><span className="file__size">{fileSize(f.size)}</span>
              <button type="button" className="btn btn-ghost btn-sm" aria-label={`Remove ${f.name}`}
                onClick={() => onChange(files.filter((x) => x !== f))}><X size={15} /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Success({ r: submitted, onAnother }: { r: RequestDetail; onAnother: () => void }) {
  // The email goes out just after the save; check back until it's Sent or Failed.
  const { data } = useQuery({
    queryKey: ["request", submitted.request_number], queryFn: () => getRequest(submitted.request_number), initialData: submitted,
    refetchInterval: (q) => (q.state.data?.email_status === "Pending" ? 2000 : false),
  });
  const r = data ?? submitted;
  return (
    <div className="page">
      <div className="card success">
        <div className="success__icon"><CheckCircle2 size={40} aria-hidden /></div>
        <h1>Requisition submitted</h1>
        <div className="ref">{r.request_number}</div>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          {r.supplier_name} · {money(r.amount)} · {r.status}
        </p>
        <p style={{ margin: "0 0 18px" }}><EmailBadge status={r.email_status} /></p>
        <p className="muted">
          {r.email_status === "Sent"
            ? "Procurement has your requisition and documents. You were copied on the email, and replies from Procurement come straight to you."
            : r.email_status === "Pending"
              ? "Your requisition is saved. Emailing it to Procurement now; this usually takes a few seconds."
              : "Your requisition is saved. The email to Procurement didn't go out yet; the Procurement team can see it and will resend it."}
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 22 }}>
          <Link to={`/requests/${r.request_number}`} className="btn btn-primary">View requisition</Link>
          <button type="button" className="btn btn-secondary" onClick={onAnother}>Submit another</button>
        </div>
      </div>
    </div>
  );
}

export default function NewRequest() {
  const { data: me } = useMe();
  const toast = useToast();
  const qc = useQueryClient();
  const { data: lists, isLoading, error } = useQuery({ queryKey: ["lookups"], queryFn: getLookups, staleTime: 5 * 60_000 });
  const [agreementFiles, setAgreementFiles] = useState<File[]>([]);
  const [supportingFiles, setSupportingFiles] = useState<File[]>([]);
  const [done, setDone] = useState<RequestDetail | null>(null);

  const { register, control, handleSubmit, watch, reset, formState: { errors, isValid, isDirty } } = useForm<FormValues>({
    resolver: zodResolver(schema), defaultValues: DEFAULTS, mode: "onChange",
  });
  const [isExtension, isCapital, signed, urgent, amount, approver] =
    watch(["is_po_extension", "is_capital_project", "agreement_signed", "is_urgent", "amount", "approver_email"]);

  const mutation = useMutation({
    mutationFn: (v: FormValues) => {
      const blankToNull = (s?: string) => (s ? s : null);
      const { other_approver_email, ...rest } = v;
      const notListed = v.approver_email === NOT_LISTED.code;
      return submitRequest({
        ...rest,
        approver_email: notListed ? other_approver_email : v.approver_email,
        approver_not_listed: notListed,
        po_recipients: splitEmails(v.po_recipients),
        previous_po: blankToNull(v.previous_po),
        // Hidden answers are dropped so a stale value can't fail server validation.
        capital_project_number: v.is_capital_project ? blankToNull(v.capital_project_number) : null,
        capital_cost_center: v.is_capital_project ? blankToNull(v.capital_cost_center) : null,
        legal_review: v.amount >= HIGH_VALUE_AMOUNT ? v.legal_review ?? null : null,
        signature_date: blankToNull(v.signature_date), urgent_reason: blankToNull(v.urgent_reason),
        description: blankToNull(v.description),
      }, agreementFiles, supportingFiles);
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["requests"] });
      setDone(r);
      window.scrollTo({ top: 0 });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Submit failed", true),
  });
  // Submit unlocks only once everything required passes validation.
  // The agreement is required once it's signed; otherwise at least one document in either section.
  const docsMissing = signed ? agreementFiles.length === 0 : agreementFiles.length + supportingFiles.length === 0;
  const docsError = signed ? "Attach the fully executed agreement." : "Attach at least one document (agreement or supporting).";
  const canSubmit = isValid && !docsMissing && !mutation.isPending;
  const touchedDocs = isDirty;

  const onSubmit = handleSubmit((v) => mutation.mutate(v));

  if (done) return <Success r={done} onAnother={() => { setDone(null); reset(DEFAULTS); setAgreementFiles([]); setSupportingFiles([]); }} />;
  if (isLoading) return <PageLoader />;
  if (error || !lists) return <div className="page"><ErrorBox error={error} /></div>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>New IT procurement requisition</h1>
          <p>Fields marked <span className="req">*</span> are required. Submit unlocks once every required field is complete.</p>
        </div>
      </div>

      <form className="card" onSubmit={onSubmit} noValidate>
        <Section step={1} title="Stakeholders" intro="Who approves in Oracle, and who should receive the final PO.">
          <Field label="Requester" htmlFor="requester">
            <input id="requester" className="input" value={me ? `${me.name} (${me.email})` : ""} disabled />
          </Field>
          <Field label="Approver" required error={errors.approver_email} htmlFor="approver_email" hint="Not listed? Choose 'Not in the list' at the end.">
            <Controller control={control} name="approver_email" render={({ field }) => (
              <Combobox id="approver_email" options={[...lists.approvers, NOT_LISTED]} value={field.value} invalid={!!errors.approver_email}
                onChange={(c) => { field.onChange(c); field.onBlur(); }} placeholder="Search by name or email" />
            )} />
          </Field>
          {approver === NOT_LISTED.code && (<>
            <div className="field" />
            <Field label="Approver email" required error={errors.other_approver_email} htmlFor="other_approver_email"
              hint="Email address of the approver who isn't in the list">
              <input id="other_approver_email" className="input" type="email" maxLength={320}
                {...register("other_approver_email")} placeholder="name@ges.com" />
            </Field>
          </>)}
          <Field label="Additional PO recipients" span error={errors.po_recipients} htmlFor="po_recipients"
            hint="Optional. Email addresses of people who should receive the final PO, separated by commas.">
            <input id="po_recipients" className="input" {...register("po_recipients")} placeholder="name@ges.com, other@ges.com" />
          </Field>
        </Section>

        <Section step={2} title="Supplier requisition detail" intro="The supplier, the SOW / quote / order form, and the documents.">
          <Field label="Supplier name" required span error={errors.supplier_name} htmlFor="supplier_name">
            <input id="supplier_name" className="input" maxLength={200} {...register("supplier_name")} />
          </Field>
          <div className="alert alert-info" style={{ gridColumn: "1 / -1" }}>
            <InfoPopover label="How to get a new supplier set up">
              <h4>New supplier? Get it set up first</h4>
              Email <a href="mailto:NAPurchasing@ges.com?subject=New%20supplier%20setup%20request">NAPurchasing@ges.com</a> with:
              <ul>
                <li>Company</li>
                <li>Contact name</li>
                <li>Contact email address</li>
                <li>Phone #</li>
                <li>Business need</li>
              </ul>
            </InfoPopover>
            <div>The supplier must be set up in Oracle before a PO can be raised. Select the <strong>i</strong> for how to request a new supplier.</div>
          </div>
          <Field label="Service start date" required error={errors.start_date} htmlFor="start_date">
            <input id="start_date" type="date" className="input" {...register("start_date", { deps: ["end_date"] })} />
          </Field>
          <Field label="Service end date" required error={errors.end_date} htmlFor="end_date">
            <input id="end_date" type="date" className="input" {...register("end_date")} />
          </Field>
          <Field label="Agreement signed" required error={errors.agreement_signed}>
            <YesNo control={control} name="agreement_signed" label="Agreement signed" />
          </Field>
          {signed ? (
            <Field label="Date of signature" required error={errors.signature_date} htmlFor="signature_date">
              <input id="signature_date" type="date" className="input" max={today()} {...register("signature_date")} />
            </Field>
          ) : <div className="field" />}
          <Field label="Total order form / SOW value (USD)" required error={errors.amount} htmlFor="amount"
            hint={amount > 0 ? `${money(amount)} · must match the supporting documents` : "Must match the supporting documents"}>
            <input id="amount" type="number" min="0" step="0.01" inputMode="decimal" className="input" {...register("amount", { valueAsNumber: true })} />
          </Field>
          {amount >= HIGH_VALUE_AMOUNT ? (
            <Field label="Legal / procurement review" required error={errors.legal_review} htmlFor="legal_review"
              hint="Required for $250,000 or more">
              <Controller control={control} name="legal_review" render={({ field }) => (
                <select id="legal_review" className="select" value={field.value === undefined ? "" : field.value ? "yes" : "no"}
                  onChange={(e) => field.onChange(e.target.value === "" ? undefined : e.target.value === "yes")} onBlur={field.onBlur}>
                  <option value="">Select…</option><option value="yes">Yes</option><option value="no">No</option>
                </select>
              )} />
            </Field>
          ) : <div className="field" />}
          <FileDrop id="agreement" label="Fully Executed Agreement" required={!!signed} max={MAX_AGREEMENT_FILES}
            hint={signed ? "The signed SOW, quote or order form" : "Required once the agreement is signed"}
            files={agreementFiles} onChange={setAgreementFiles}
            error={signed && docsMissing && touchedDocs ? docsError : undefined} />
          <FileDrop id="supporting" label="Supporting Documents" required={false} max={MAX_SUPPORTING_FILES}
            hint="Optional: quotes, unsigned drafts, backup"
            files={supportingFiles} onChange={setSupportingFiles}
            error={!signed && docsMissing && touchedDocs ? docsError : undefined} />
        </Section>

        <Section step={3} title="Project costing" intro="Where the cost is charged. Project and task numbers keep their leading zeroes.">
          <Field label="PO extension / add-on" required error={errors.is_po_extension}>
            <YesNo control={control} name="is_po_extension" label="PO extension / add-on" />
          </Field>
          {isExtension ? (
            <Field label="Previous PO" required error={errors.previous_po} htmlFor="previous_po">
              <input id="previous_po" className="input" maxLength={50} {...register("previous_po")} />
            </Field>
          ) : <div className="field" />}
          <Field label="Capital project" required error={errors.is_capital_project}>
            <YesNo control={control} name="is_capital_project" label="Capital project" />
          </Field>
          <div className="field" />
          {isCapital && (<>
            <Field label="Capital project number" required error={errors.capital_project_number} htmlFor="capital_project_number" hint="Exactly 9 digits">
              <input id="capital_project_number" className="input mono" inputMode="numeric" maxLength={9} {...register("capital_project_number")} />
            </Field>
            <Field label="Capital task / cost center" required error={errors.capital_cost_center} htmlFor="capital_cost_center" hint="Exactly 4 digits">
              <input id="capital_cost_center" className="input mono" inputMode="numeric" maxLength={4} {...register("capital_cost_center")} />
            </Field>
          </>)}
          <Field label="Project number" required error={errors.project_number} htmlFor="project_number" hint="Exactly 9 digits">
            <input id="project_number" className="input mono" inputMode="numeric" maxLength={9} {...register("project_number")} />
          </Field>
          <Field label="Task / cost center" required error={errors.cost_center} htmlFor="cost_center" hint="Exactly 4 digits">
            <input id="cost_center" className="input mono" inputMode="numeric" maxLength={4} {...register("cost_center")} />
          </Field>
        </Section>

        <Section step={4} title="Urgency & notes" intro="Mark the request urgent only when there's a real deadline or risk.">
          <div className="field"><Toggle label="Urgent request" {...register("is_urgent")} /></div>
          {urgent ? (
            <Field label="Urgent reason" required error={errors.urgent_reason} htmlFor="urgent_reason">
              <select id="urgent_reason" className="select" {...register("urgent_reason")}>
                <option value="">Select…</option>{lists.urgent_reasons.map((r) => <option key={r}>{r}</option>)}
              </select>
            </Field>
          ) : <div className="field" />}
          <Field label="Description / notes" span error={errors.description} htmlFor="description" hint="Optional. Anything Procurement should know.">
            <textarea id="description" className="textarea" maxLength={5000} {...register("description")} />
          </Field>
        </Section>

        <div className="form-footer">
          <span className="small muted" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Mail size={16} aria-hidden />
            {canSubmit || mutation.isPending
              ? "Procurement is notified and you're copied. Replies come to you."
              : docsMissing && isValid ? docsError : "Complete every required field and attach the documents to submit."}
          </span>
          <div style={{ display: "flex", gap: 10 }}>
            <button type="button" className="btn btn-secondary" disabled={mutation.isPending}
              onClick={() => { reset(DEFAULTS); setAgreementFiles([]); setSupportingFiles([]); }}>Clear</button>
            <button type="submit" className="btn btn-primary btn-lg" disabled={!canSubmit}>
              {mutation.isPending ? <><Spinner /> Submitting…</> : <><Send size={17} aria-hidden /> Submit requisition</>}
            </button>
          </div>
        </div>
        {mutation.isError && (
          <div style={{ padding: "0 24px 20px" }}>
            <div className="alert alert-danger"><AlertTriangle size={18} aria-hidden /><div>{(mutation.error as Error).message}</div></div>
          </div>
        )}
      </form>
    </div>
  );
}
