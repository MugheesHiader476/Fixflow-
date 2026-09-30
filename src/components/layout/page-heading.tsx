import type { ReactNode } from "react";

export function PageHeading({ eyebrow, title, children, action }: {
  eyebrow: string;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="ff-page-heading">
      <div>
        <p className="ff-eyebrow"><span aria-hidden="true" />{eyebrow}</p>
        <h1>{title}</h1>
        <p className="ff-page-description">{children}</p>
      </div>
      {action && <div className="ff-page-action">{action}</div>}
    </header>
  );
}
