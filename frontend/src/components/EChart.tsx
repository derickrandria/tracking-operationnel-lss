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
    const chart = echarts.init(ref.current, theme === "dark" ? "dark" : undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    chartRef.current?.setOption({ backgroundColor: "transparent", ...option }, true);
  }, [option, theme]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
