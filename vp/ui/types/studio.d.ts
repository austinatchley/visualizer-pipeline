// Ambient types for the studio frontend (vp/ui/static/app.js), checked by `tsc`.
// Dev-only: nothing here is served or emitted.
//
// API shapes come from api.d.ts, which is GENERATED from the Pydantic models in
// vp/ui/models.py (`python scripts/gen_api_types.py`). Don't hand-edit API types
// here: change the model, regenerate, and let `tsc` flag every stale use.

// --- API (aliases of generated schemas) ----------------------------------------

type Schemas = import("./api").components["schemas"];

type BootData = Schemas["BootData"];
type StyleFieldSpec = Schemas["StyleFieldSpec"];
type TrackSummary = Schemas["TrackSummary"];
type TrackError = Schemas["TrackError"];
type CreatedTrack = Schemas["CreatedTrack"];
type TrackDetail = Schemas["TrackDetail"];
type ImagesAdded = Schemas["ImagesAdded"];
type ImagesRemoved = Schemas["ImagesRemoved"];
type ImageOrder = Schemas["ImageOrder"];
type MetadataResult = Schemas["MetadataResult"];
type StyleSaveResult = Schemas["StyleSaveResult"];
type PreviewResult = Schemas["PreviewResult"];
type JobCreated = Schemas["JobCreated"];
type JobSummary = Schemas["JobSummary"];
type JobStatus = JobSummary["status"];

/** A /api/tracks entry; `error` is only set on entries that failed to load. */
type TrackListEntry = (TrackSummary & { error?: undefined }) | TrackError;

// --- Client-side style model ----------------------------------------------------

type StyleValue = string | number | boolean | null;
/**
 * The editor's working style. Looser than the generated `Style` because controls
 * are built from /api/boot `fields` and index it by key; the server validates it.
 */
type Style = Record<string, StyleValue>;

// --- Client state ------------------------------------------------------------

interface JobRow {
  label: HTMLElement;
  status: HTMLElement;
  fill: HTMLElement;
  err: HTMLElement;
  cancel: HTMLButtonElement;
  cancelling: boolean;
  video: HTMLVideoElement;
  url: string | null;
  lastStatus: JobStatus | null;
}

interface StudioState {
  /** Loaded by init() before anything reads it. */
  boot: BootData;
  tracks: TrackListEntry[];
  current: TrackDetail | null;
  model: Style;
  jobRows?: Record<string, JobRow>;
  dropFiles: File[];
  cmpA?: Style;
  cmpB?: Style;
  cmpIds?: string[];
  cmpTimer?: ReturnType<typeof setInterval>;
}

/** The toast element keeps its hide timer on itself. */
interface HTMLElement {
  _t?: ReturnType<typeof setTimeout>;
}
