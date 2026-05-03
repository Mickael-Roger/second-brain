// Brand logo. Picks the variant whose background blends with the
// active theme (the source PNGs ship with their own bg).

import logoBlack from "@/assets/logo_black.png";
import logoWhite from "@/assets/logo_white.png";
import { useTheme } from "@/lib/theme";

interface Props {
  className?: string;
  alt?: string;
}

export default function Logo({ className, alt = "Second Brain" }: Props) {
  const theme = useTheme();
  const src = theme === "light" ? logoWhite : logoBlack;
  return <img src={src} alt={alt} className={className} draggable={false} />;
}
