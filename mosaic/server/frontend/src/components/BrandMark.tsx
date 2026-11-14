import { useId } from "react";

type Props = {
  size?: number;
  className?: string;
};

function hexPoints(cx: number, cy: number, r: number): string {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 30);
    return `${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`;
  }).join(" ");
}

export default function BrandMark({ size = 42, className }: Props) {
  const raw = useId().replace(/:/g, "");
  const bg = `bm-bg-${raw}`;
  const gold = `bm-gold-${raw}`;
  const sheen = `bm-sheen-${raw}`;
  const r = 6.15;
  const dx = r * Math.sqrt(3);
  const dy = r * 1.5;
  const cx = 24;
  const cy = 24.2;
  const ring = [
    [cx + dx, cy],
    [cx + dx / 2, cy + dy],
    [cx - dx / 2, cy + dy],
    [cx - dx, cy],
    [cx - dx / 2, cy - dy],
    [cx + dx / 2, cy - dy],
  ] as const;
  const ringFill = ["#E8F1FA", "#C9DDF0", "#F4E4B4", "#D7EAF4", "#B9D0E8", "#E7F4F2"];

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <defs>
        <linearGradient id={bg} x1="8" y1="2" x2="44" y2="46" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1E5FA8" />
          <stop offset="0.48" stopColor="#0E3A72" />
          <stop offset="1" stopColor="#071E42" />
        </linearGradient>
        <linearGradient id={gold} x1="18" y1="16" x2="30" y2="32" gradientUnits="userSpaceOnUse">
          <stop stopColor="#F7E3A4" />
          <stop offset="1" stopColor="#C9A14B" />
        </linearGradient>
        <linearGradient id={sheen} x1="10" y1="4" x2="24" y2="22" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ffffff" stopOpacity="0.28" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="14" fill={`url(#${bg})`} />
      <rect x="1.4" y="1.4" width="45.2" height="45.2" rx="12.6" stroke="rgba(255,255,255,0.28)" strokeWidth="1.2" />
      <path d="M8 11.5C14 7.5 22 8 26 11" stroke={`url(#${sheen})`} strokeWidth="5" strokeLinecap="round" />
      {ring.map(([x, y], i) => (
        <polygon key={i} points={hexPoints(x, y, r - 0.15)} fill={ringFill[i]} opacity="0.92" />
      ))}
      <polygon points={hexPoints(cx, cy, r + 0.35)} fill={`url(#${gold})`} />
      <polygon points={hexPoints(cx, cy, r - 2.35)} fill="#FFF7DF" opacity="0.55" />
    </svg>
  );
}
