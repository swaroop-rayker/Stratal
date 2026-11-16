// Right-side drawer (DESIGN-SYSTEM c) as a modal dialog on Radix: focus moves in, is
// trapped, and returns to the opener on close; Esc closes (SCR-02 accessibility notes).
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Icon } from "./ui";

export function Drawer({ open, onOpenChange, title, children, footer, width }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-scrim" />
        <Dialog.Content className="drawer" style={width ? { width } : undefined} aria-describedby={undefined}>
          <header>
            <Dialog.Title asChild>
              <h2>{title}</h2>
            </Dialog.Title>
            <span className="spacer" />
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
