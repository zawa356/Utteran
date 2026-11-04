import { useSyncExternalStore } from "react";
import { Alert, Button, Chip, FormControl, FormControlLabel, InputLabel, List, ListItemButton, ListItemText, MenuItem, Select, Switch, Table, TableBody, TableCell, TableRow, ToggleButton, ToggleButtonGroup } from "@mui/material";
import { LABELS, LANGUAGES, MODELS, SECTIONS, createController, effectiveSettings, exposeForTests, isOverridden } from "../../core/core";

const c = createController();
exposeForTests(c);
c.loadEnvironment("cpu");

function Pick(props: { testid?: string; label: string; value: string; items: string[]; disabled?: boolean; onChange: (v: string) => void }) {
  const id = `pick-${props.label}-${props.testid ?? "x"}`;
  return (
    <FormControl size="small" sx={{ minWidth: 160, my: 0.5 }} disabled={props.disabled}>
      <InputLabel id={id}>{props.label}</InputLabel>
      <Select data-testid={props.testid} labelId={id} label={props.label} value={props.value} onChange={(e) => props.onChange(String(e.target.value))}>
        {props.items.map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}
      </Select>
    </FormControl>
  );
}

export function App() {
  const state = useSyncExternalStore(c.store.subscribe, c.store.get);
  const selected = state.files.find((f) => f.id === state.selectedFileId)!;
  const effective = effectiveSettings(state, selected.id);
  const overridden = isOverridden(state, selected.id, "language");
  const pending = state.submit.phase === "pending";
  return (
    <div className="shell" data-testid="ready">
      <nav className="side" aria-label="Primary">
        <ToggleButtonGroup size="small" exclusive value={state.mode} onChange={(_e, v) => v && c.setMode(v)}>
          <ToggleButton data-testid="mode-normal" value="normal">Normal</ToggleButton>
          <ToggleButton data-testid="mode-expert" value="expert">Expert</ToggleButton>
        </ToggleButtonGroup>
        <List dense>{SECTIONS.map((s) => <ListItemButton key={s} selected={s === "workspace"}><ListItemText primary={LABELS[s]} /></ListItemButton>)}</List>
      </nav>
      <main className="work">
        {state.mode === "normal" ? (
          <section className="normal">
            <h1>Transcribe</h1>
            <p>Input: {selected.name}</p>
            <p>Recommended: <b>{effective.model}</b> / {effective.language}</p>
            <details><summary>Details</summary><span data-testid="normal-route">{state.environment.data?.backend} · {state.environment.data?.device} · {state.environment.data?.reason}</span></details>
            <Button data-testid="normal-start" variant="contained" loading={pending} onClick={() => c.submit()}>Start</Button>
          </section>
        ) : (
          <div className="panes">
            <section className="pane" aria-label="Inputs">
              <h2>Inputs</h2>
              {state.files.map((f) => (
                <div key={f.id} className="file" role="option" aria-selected={f.id === state.selectedFileId} data-testid={`file-row-${f.id}`} tabIndex={0} onClick={() => c.selectFile(f.id)} onKeyDown={(e) => e.key === "Enter" && c.selectFile(f.id)}>
                  <span>{f.name}</span><Chip size="small" data-testid={`effective-language-${f.id}`} label={effectiveSettings(state, f.id).language} />
                </div>
              ))}
            </section>
            <section className="pane" aria-label="Inspector">
              <h2>Shared defaults</h2>
              <Pick testid="default-language" label="Language" items={LANGUAGES} value={state.defaults.language} onChange={(v) => c.setDefault("language", v)} />
              <Pick label="Model" items={MODELS} value={state.defaults.model} onChange={(v) => c.setDefault("model", v)} />
              <h2>Selected: {selected.name}</h2>
              <FormControlLabel label="Override language" control={<Switch data-testid="override-language" checked={overridden} onChange={(e) => c.setOverride(selected.id, "language", e.target.checked ? effective.language : undefined)} />} />
              <div><Pick testid="inspector-language" label="Language" items={LANGUAGES} disabled={!overridden} value={effective.language} onChange={(v) => c.setOverride(selected.id, "language", v)} /></div>
              <h2>Pipeline</h2>
              <p>Input → ASR ({effective.model}) → Diarization ({effective.diarization ? "on" : "off"}) → Output</p>
              <h2>Environment</h2>
              <div className="row">
                <Button data-testid="profile-cuda" size="small" variant="outlined" onClick={() => c.loadEnvironment("cuda")}>cuda</Button>
                <Button data-testid="profile-cpu" size="small" variant="outlined" onClick={() => c.loadEnvironment("cpu")}>cpu</Button>
                <span data-testid="profile-current">{state.environment.data?.profile}</span>
                {state.environment.phase === "pending" && <span data-testid="profile-pending">loading…</span>}
              </div>
            </section>
            <section className="pane queue" aria-label="Queue">
              <h2>Queue</h2>
              <div className="row">
                <Button data-testid="submit" variant="contained" loading={pending} disabled={pending} onClick={() => c.submit()}>Submit</Button>
                <span data-testid="submit-status">{state.submit.phase}</span>
              </div>
              {state.submit.phase === "error" && (
                <Alert severity="error" data-testid="submit-error" action={state.submit.error?.retryable ? <Button data-testid="retry" size="small" onClick={() => c.retry()}>Retry</Button> : undefined}>
                  {state.submit.error?.kind}: {state.submit.error?.code}
                </Alert>
              )}
              <Table size="small"><TableBody>{Object.values(state.jobs).map((j) => (
                <TableRow key={j.id} data-testid={`job-row-${j.id}`}><TableCell>{j.id}</TableCell><TableCell data-testid={`job-status-${j.id}`}>{j.status}</TableCell><TableCell>r{j.revision}</TableCell></TableRow>
              ))}</TableBody></Table>
              <p data-testid="event-stats">applied={state.stats.applied} ignored={state.stats.ignored} resyncs={state.stats.resyncs}</p>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
