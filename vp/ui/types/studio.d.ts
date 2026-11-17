// Ambient types for the studio frontend (vp/ui/static/app.js), checked by `tsc`.
// Dev-only: nothing here is served or emitted.
//
// The API shapes mirror vp/ui/models.py by hand for now; #63 replaces them with
// types generated from /openapi.json so drift fails CI.

// --- API -------------------------------------------------------------------

type StyleValue = string | number | boolean | null;
/** A resolved style: every key of DEFAULT_STYLE (see /api/boot `fields`). */
type Style = Record<string, StyleValue>;

interface StyleFieldSpec {
  type: "preset" | "int" | "float" | "bool" | "choice" | "path" | "texture" | "text";
  help: string;
  min?: number;
  max?: number;
  step?: number;
  exclusive_min?: boolean;
  choices?: (string | number)[];
}

interface BootData {
  fields: Record<string, StyleFieldSpec>;
  defaults: Style;
  presets: string[];
  textures: { path: string; label: string }[];
  aspects: Record<string, { label: string; kind: string }>;
}

interface TrackSummary {
  id: string;
  title: string;
  artist: string | null;
  path: string;
  audio: string;
  image_count: number;
  preset: string;
  error?: undefined;
}

/** Catalog entry for a folder that failed to load. */
interface TrackError {
  id: string;
  title: string;
  path: string;
  error: string;
}

type TrackListEntry = TrackSummary | TrackError;

interface CreatedTrack {
  id: string;
  title: string;
  path: string;
  audio: string;
  image_count: number;
}

interface Hook {
  mode: "auto" | "full" | "manual";
  target: number;
  start: number | null;
  end: number | null;
}

interface TrackDetail {
  id: string;
  title: string;
  artist: string | null;
  handle: string | null;
  path: string;
  audio: string;
  images: string[];
  image_count: number;
  style: Style;
  hook: Hook;
}

/** Image add/remove/reorder responses all carry the new order. */
interface ImagesResult {
  images: string[];
  added?: string[];
  removed?: string[];
}

interface MetadataResult {
  id: string;
  title: string;
  artist: string | null;
  handle: string | null;
}

interface StyleSaveResult {
  saved: Record<string, StyleValue>;
  resolved: Style;
}

interface PreviewResult {
  urls: Record<string, string>;
  url: string;
}

interface JobCreated {
  id: string;
}

type JobStatus = "pending" | "running" | "done" | "skipped" | "cancelled" | "failed";

interface JobSummary {
  id: string;
  track: string;
  aspect: string;
  preview: boolean;
  status: JobStatus;
  progress: number;
  error: string | null;
  url: string | null;
  created: number;
  started: number | null;
  ended: number | null;
}

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
