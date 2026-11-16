// Modal (DESIGN-SYSTEM c) on Radix Dialog: focus is trapped inside and returns to the
// trigger on close; Esc closes.
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

export function Modal({ open, onOpenChange, title, icon, children, footer, width }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" style={{ position: "fixed" }} />
        <Dialog.Content
          className="modal"
          style={{
            position: "fixed",
            left: "50%",
            top: "50%",
            transform: "translate(-50%, -50%)",
            zIndex: "var(--z-modal)",
            width,
          }}
          aria-describedby={undefined}
        >
          <header>
            {icon}
            <Dialog.Title style={{ font: "inherit", margin: 0 }}>{title}</Dialog.Title>
          </header>
          <div>{children}</div>
          {footer ? <footer>{footer}</footer> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const ModalClose = Dialog.Close;
