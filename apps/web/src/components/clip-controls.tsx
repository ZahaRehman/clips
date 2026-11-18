import type { ClipExport, CropMode } from "@/lib/api";

type Props = {
  selectedId: string | null;
  busy: boolean;
  cropMode: CropMode;
  captionText: string;
  customX: number;
  customY: number;
  customW: number;
  customH: number;
  exportsByClip: Record<string, ClipExport[]>;
  onSaveCrop: () => void;
  onSaveCaption: () => void;
  setCropMode: (value: CropMode) => void;
  setCaptionText: (value: string) => void;
  setCustomX: (value: number) => void;
  setCustomY: (value: number) => void;
  setCustomW: (value: number) => void;
  setCustomH: (value: number) => void;
};

export function cropObjectPosition(props: Pick<Props, "cropMode" | "customX" | "customY" | "customW" | "customH">): string {
  if (props.cropMode === "left") return "left center";
  if (props.cropMode === "right") return "right center";
  if (props.cropMode === "custom") {
    const cx = (props.customX + props.customW / 2) * 100;
    const cy = (props.customY + props.customH / 2) * 100;
    return `${cx}% ${cy}%`;
  }
  return "center center";
}

export function CropCaptionControls(props: Props) {
  const stale = props.selectedId ? props.exportsByClip[props.selectedId]?.[0]?.status === "pending" : false;
  return (
    <div style={{ marginTop: "1rem" }}>
      <h2>Crop and captions</h2>
      <div className="row-inputs">
        <label>
          Crop
          <select value={props.cropMode} onChange={(e) => props.setCropMode(e.target.value as CropMode)}>
            <option value="face">Face</option>
            <option value="center">Center</option>
            <option value="left">Left</option>
            <option value="right">Right</option>
            <option value="custom">Custom</option>
            <option value="full">Whole screen (16:9)</option>
          </select>
        </label>
        {props.cropMode === "custom" && (
          <>
            <label>
              Left
              <input type="number" step="0.01" min="0" max="1" value={props.customX} onChange={(e) => props.setCustomX(Number(e.target.value))} />
            </label>
            <label>
              Top
              <input type="number" step="0.01" min="0" max="1" value={props.customY} onChange={(e) => props.setCustomY(Number(e.target.value))} />
            </label>
            <label>
              Width
              <input type="number" step="0.01" min="0" max="1" value={props.customW} onChange={(e) => props.setCustomW(Number(e.target.value))} />
            </label>
            <label>
              Height
              <input type="number" step="0.01" min="0" max="1" value={props.customH} onChange={(e) => props.setCustomH(Number(e.target.value))} />
            </label>
          </>
        )}
        <button className="secondary" disabled={props.busy} onClick={props.onSaveCrop}>
          Save crop
        </button>
      </div>
      <p className="muted">
        Face keeps the largest face, then the median center. No face falls back to center. Left, right, and center are fixed frames.
      </p>
      <label className="stack">
        Caption text
        <textarea className="caption-box" value={props.captionText} onChange={(e) => props.setCaptionText(e.target.value)} />
      </label>
      <div className="actions">
        <button className="secondary" disabled={props.busy} onClick={props.onSaveCaption}>
          Save captions
        </button>
      </div>
      {stale && <p className="muted">This export is out of date. Re-export to apply the latest trim, crop, and captions.</p>}
    </div>
  );
}
