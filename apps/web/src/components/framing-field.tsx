"use client";

export type Framing = "face" | "gameplay" | "full";

type Props = {
  value: Framing;
  disabled?: boolean;
  onChange: (value: Framing) => void;
};

export function FramingField({ value, disabled, onChange }: Props) {
  return (
    <label className="field">
      Frame
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(framingOf(event.target.value))}
      >
        <option value="face">Focus on the face</option>
        <option value="gameplay">Keep the gameplay</option>
        <option value="full">Whole screen (16:9)</option>
      </select>
      <span className="muted small">
        {value === "full"
          ? "Keeps the whole stream screen as a landscape 1920×1080 video. Nothing is cropped."
          : value === "gameplay"
            ? "The short stays on the game. A webcam in the corner is not the subject."
            : "The short follows the person. Use this for a talking-head video."}
      </span>
    </label>
  );
}

export function framingOf(value: unknown): Framing {
  return value === "gameplay" || value === "full" ? value : "face";
}
