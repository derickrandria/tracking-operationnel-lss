/** Enveloppe React minimale pour Apache ECharts. */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { useTheme } from "../theme";

export default function EChart({ option, height = 260 }: { option: any; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!ref.current) return;
    try {
      const existing = echarts.getInstanceByDom(ref.current);
      if (existing) {
        existing.dispose();
      }
      const chart = echarts.init(ref.current, theme === "dark" ? "dark" : undefined, { renderer: "canvas" });
      chartRef.current = chart;
      const ro = new ResizeObserver(() => {
        try { chart.resize(); } catch {}
      });
      ro.observe(ref.current);
      return () => {
        ro.disconnect();
        try { chart.dispose(); } catch {}
        chartRef.current = null;
      };
    } catch (err) {
      console.warn("Erreur initialisation ECharts:", err);
    }
  }, [theme]);

  useEffect(() => {
    try {
      chartRef.current?.setOption({ backgroundColor: "transparent", ...option }, true);
    } catch (err) {
      console.warn("Erreur setOption ECharts:", err);
    }
  }, [option, theme]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
