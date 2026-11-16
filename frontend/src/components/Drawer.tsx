// Right-side drawer (DESIGN-SYSTEM c) as a modal dialog on Radix: focus moves in, is
// trapped, and returns to the opener on close; Esc closes (SCR-02 accessibility notes).
import * as Dialog from "@radix-ui/react-dialog";
import type { KeyboardEvent, ReactNode } from "react";

import { Icon } from "./ui";

export function Drawer({ open, onOpenChange, title, heading, keys, className, children, footer, width, onKeyDown }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The accessible title; shown as an h2 unless `heading` replaces it. */
  title: string;
  heading?: ReactNode;
  /** Extra key hints before Esc (the row drawer's j k). */
  keys?: ReactNode;
  className?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  onKeyDown?: (event: KeyboardEvent<HTMLDivElement>) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-scrim" />
        <Dialog.Content
          className={className ? `drawer ${className}` : "drawer"}
          style={width ? { width } : undefined}
          aria-describedby={undefined}
          onKeyDown={onKeyDown}
        >
          <header>
            <Dialog.Title asChild>{heading ?? <h2>{title}</h2>}</Dialog.Title>
            <span className="spacer" />
            {keys}
            <kbd>Esc</kbd>
            <Dialog.Close className="btn sm ghost" aria-label="Close">
              <Icon name="x" />
            </Dialog.Close>
          </header>
          <div className="body">{children}</div>
          {footer ? <footer>{footer}</footer> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const DrawerClose = Dialog.Close;
