import React from "react";

// Line icons, 24×24, 1.6 stroke, rounded — drawn inline (no icon font or CDN).
const icons: Record<string, React.ReactNode> = {
    home: (
        <>
            <path d="m3 11 9-8 9 8" />
            <path d="M5 10v11h5v-7h4v7h5V10" />
        </>
    ),
    gift: (
        <>
            <rect x="3" y="8" width="18" height="13" rx="2" />
            <path d="M12 8v13M3 12h18" />
            <path d="M12 8C3 8 6 0 10 4l2 4c9 0 6-8 2-4z" />
        </>
    ),
    user: (
        <>
            <circle cx="12" cy="7" r="4" />
            <path d="M4 22v-3a8 8 0 0 1 16 0v3z" />
        </>
    ),
    users: (
        <>
            <circle cx="9" cy="8" r="3.5" />
            <path d="M2.5 20v-1.5A5.5 5.5 0 0 1 8 13h2a5.5 5.5 0 0 1 5.5 5.5V20" />
            <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 13a5 5 0 0 1 3.5 5V20" />
        </>
    ),
    arrow: <path d="m9 5 7 7-7 7" />,
    phone: (
        <>
            <rect x="6" y="2" width="12" height="20" rx="3" />
            <path d="M10 18h4" />
        </>
    ),
    wave: <path d="M4 15v6M10 8v13M16 3v18M22 11v10" />,
    moon: <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z" />,
    sun: (
        <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </>
    ),
    rocket: (
        <>
            <path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2" />
            <path d="M9 18l-3-3c1-4 4-9 12-11-2 8-7 11-11 12z" />
            <circle cx="14.5" cy="9.5" r="1.6" />
        </>
    ),
    copy: (
        <>
            <rect x="8" y="8" width="13" height="13" rx="2" />
            <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
        </>
    ),
    clock: (
        <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
        </>
    ),
    link: (
        <>
            <path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L12 5.6" />
            <path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2" />
        </>
    ),
    alert: (
        <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7.5v5.5M12 16.5v.01" />
        </>
    ),
    info: (
        <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v6M12 7.5v.01" />
        </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    minus: <path d="M5 12h14" />,
    refresh: (
        <>
            <path d="M20 11a8 8 0 0 0-14.3-4.5L4 8" />
            <path d="M4 3v5h5M4 13a8 8 0 0 0 14.3 4.5L20 16" />
            <path d="M20 21v-5h-5" />
        </>
    ),
    logout: (
        <>
            <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
            <path d="M10 8l-4 4 4 4M6 12h10" />
        </>
    ),
    folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
};

export function Icon({ name }: { name: string }) {
    return (
        <svg
            className="icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            {icons[name]}
        </svg>
    );
}
