import { BRAND_MARK_DARK, BRAND_MARK_LIGHT } from "@/brand";
import { useTheme } from "@/theme-context";

interface BrandMarkProps {
  alt?: string;
  className?: string;
}

export function BrandMark({ alt = "", className }: BrandMarkProps) {
  const { theme } = useTheme();
  return (
    <img
      src={theme === "dark" ? BRAND_MARK_DARK : BRAND_MARK_LIGHT}
      alt={alt}
      className={className}
    />
  );
}
