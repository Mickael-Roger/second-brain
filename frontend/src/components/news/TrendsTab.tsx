// Bubble dashboard sub-tab. Hot-topic hashtags sized by article count;
// hover reveals the underlying articles. The shared period selector
// and fetch/cluster triggers live in the parent NewsView header.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Newspaper } from "lucide-react";

import { api, type NewsTrend } from "@/lib/api";

import TrendBubble from "./EventBubble";
import { packCircles } from "./pack";

type Period = "today" | "7d" | "30d" | "custom";

interface Props {
  period: Period;
  customFrom: string;
  customTo: string;
}

export default function TrendsTab({ period, customFrom, customTo }: Props) {
  const { t } = useTranslation();
  const [hoveredTag, setHoveredTag] = useState<string | null>(null);

  const queryKey = useMemo(() => {
    if (period === "custom") return ["news-trends", "custom", customFrom, customTo];
    return ["news-trends", period];
  }, [period, customFrom, customTo]);

  const trends = useQuery<NewsTrend[]>({
    queryKey,
    queryFn: () => {
      const qs = new URLSearchParams({ period });
      if (period === "custom") {
        qs.set("from", customFrom);
        qs.set("to", customTo);
      }
      return api.get<NewsTrend[]>(`/api/news/trends?${qs.toString()}`);
    },
  });

  if (trends.isLoading) {
    return <p className="px-4 py-4 text-sm text-muted">{t("news.loading")}</p>;
  }
  if (!trends.data || trends.data.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div className="max-w-md space-y-3">
          <Newspaper className="mx-auto h-8 w-8 text-accent" />
          <p className="text-sm text-muted">{t("news.empty")}</p>
        </div>
      </div>
    );
  }

  return (
    <BubbleCanvas
      trends={trends.data}
      period={period}
      customFrom={customFrom}
      customTo={customTo}
      hoveredTag={hoveredTag}
      onHover={setHoveredTag}
    />
  );
}

interface CanvasProps {
  trends: NewsTrend[];
  period: Period;
  customFrom: string;
  customTo: string;
  hoveredTag: string | null;
  onHover: (tag: string | null) => void;
}

function BubbleCanvas({
  trends,
  period,
  customFrom,
  customTo,
  hoveredTag,
  onHover,
}: CanvasProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  useEffect(() => {
    if (!ref.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) {
        const cr = e.contentRect;
        setSize({ w: Math.max(200, cr.width), h: Math.max(200, cr.height) });
      }
    });
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  // sqrt scaling keeps bubble AREA proportional to article count —
  // that's what readers instinctively decode.
  const packed = useMemo(() => {
    if (trends.length === 0) return [];
    const maxCount = Math.max(...trends.map((trend) => trend.count));
    const minR = 28;
    const maxR = Math.min(size.w, size.h) / 5;
    const radii = trends.map((trend) => {
      const ratio = Math.sqrt(trend.count / maxCount);
      return minR + ratio * (maxR - minR);
    });
    return packCircles(radii, size.w, size.h);
  }, [trends, size]);

  return (
    <div ref={ref} className="relative h-full w-full overflow-hidden">
      {trends.map((trend, i) => {
        const c = packed[i];
        if (!c) return null;
        return (
          <TrendBubble
            key={trend.tag}
            tag={trend.tag}
            count={trend.count}
            period={period}
            customFrom={customFrom}
            customTo={customTo}
            x={c.x}
            y={c.y}
            r={c.r}
            hovered={hoveredTag === trend.tag}
            onHoverStart={() => onHover(trend.tag)}
            onHoverEnd={() => onHover(hoveredTag === trend.tag ? null : hoveredTag)}
          />
        );
      })}
    </div>
  );
}
