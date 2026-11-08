import React from "react";
import { FLAG_HEIGHT, FLAG_WIDTH, flagShapes } from "./flags.mjs";

type Shape = { x: number; y: number; width: number; height: number; fill: string };

/** Country flag for a server; a neutral globe when the country is unknown or absent. */
export function Flag({ code, width = 28 }: { code?: string | null; width?: number }) {
    const shapes: Shape[] | null = flagShapes(code ?? null);
    const height = Math.round((width * FLAG_HEIGHT) / FLAG_WIDTH);
    return (
        <svg
            className="flag"
            width={width}
            height={height}
            viewBox={`0 0 ${FLAG_WIDTH} ${FLAG_HEIGHT}`}
            role="img"
            aria-label={code ? `Флаг ${code}` : "Страна не указана"}
            shapeRendering="crispEdges"
        >
            <clipPath id="flag-clip">
                <rect width={FLAG_WIDTH} height={FLAG_HEIGHT} rx="1.6" />
            </clipPath>
            <g clipPath="url(#flag-clip)">
                {shapes ? (
                    shapes.map((s, i) => <rect key={i} {...s} />)
                ) : (
                    <>
                        <rect width={FLAG_WIDTH} height={FLAG_HEIGHT} fill="#6f6785" />
                        <circle cx="10" cy="7" r="4.6" fill="none" stroke="#e7def7" strokeWidth="1" />
                        <ellipse cx="10" cy="7" rx="2" ry="4.6" fill="none" stroke="#e7def7" strokeWidth="0.8" />
                        <path d="M5.4 7h9.2" stroke="#e7def7" strokeWidth="0.8" />
                    </>
                )}
            </g>
        </svg>
    );
}
