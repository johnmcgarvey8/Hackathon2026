import type { SVGProps } from "react";

export type IconName =
  | "dashboard"
  | "chat"
  | "control"
  | "cms"
  | "integrations"
  | "menu"
  | "search"
  | "plus"
  | "spark"
  | "sources"
  | "send"
  | "close"
  | "trash"
  | "trend"
  | "lock"
  | "chevron";

const paths: Record<IconName, React.ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  chat: <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />,
  control: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" /><path d="M12 3v3m0 12v3M3 12h3m12 0h3" /></>,
  cms: <><path d="M4 19.5V5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2.5" /><path d="M8 7h6m-6 4h6" /></>,
  integrations: <><path d="M8 3v4a4 4 0 0 0 4 4h1m3 10v-4a4 4 0 0 0-4-4h-1" /><path d="m5 5 3-3 3 3m2 14 3 3 3-3" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  spark: <><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z" /><path d="m18 15 .8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" /></>,
  sources: <><path d="M4 6c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2Z" /><path d="M4 6v6c0 1.1 3.6 2 8 2s8-.9 8-2V6M4 12v6c0 1.1 3.6 2 8 2s8-.9 8-2v-6" /></>,
  send: <path d="m5 12 14-8-5 16-3-6zM5 12h6" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  trash: <><path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7" /><path d="M10 11v6m4-6v6" /></>,
  trend: <path d="m3 17 6-6 4 4 8-9M15 6h6v6" />,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
  chevron: <path d="m9 18 6-6-6-6" />,
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg
      aria-hidden="true"
      className="ui-icon"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
