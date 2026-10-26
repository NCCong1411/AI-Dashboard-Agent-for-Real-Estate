import type {CSSProperties} from "react";

const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  chart: "M4 4v16h16 M8 15v-4 M13 15V7 M18 15v-7",
  building: "M4 21V7l8-4v18 M12 9h8v12 M2 21h20 M7 9h2 M7 13h2 M7 17h2 M15 12h2 M15 16h2",
  sparkles: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3 M20 2v4 M18 4h4",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  logout: "M9 4H4v16h5 M10 12h11 M16 7l5 5-5 5",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3 M8 12l3 3 5-6",
  filter: "M4 6h16 M7 12h10 M10 18h4",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  eyeOff: "m3 3 18 18 M10.5 5.1A12 12 0 0 1 12 5c6.5 0 10 7 10 7a19 19 0 0 1-3.2 4.2 M6.2 6.2A21 21 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.7 M10 10a3 3 0 0 0 4 4",
  check: "m5 12 4 4L19 6",
  refresh: "M20 7v5h-5 M4 17v-5h5 M5.5 7a7.5 7.5 0 0 1 12.4-1L20 9 M4 15l2.1 3A7.5 7.5 0 0 0 18.5 17",
} as const;

export function Icon({name, size = 20, className, style}: {
  name: keyof typeof paths; size?: number; className?: string; style?: CSSProperties;
}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    className={className} style={style}><path d={paths[name]} /></svg>;
}
