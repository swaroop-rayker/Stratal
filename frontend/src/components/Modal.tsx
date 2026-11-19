// Modal (DESIGN-SYSTEM c) on Radix Dialog: focus is trapped inside and returns to the
// trigger on close; Esc closes.
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode, RefObject } from "react";

export function Modal({ open, onOpenChange, title, icon, children, footer, width, initialFocus, alert }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  /** The element to focus on open, e.g. a deletion dialog's safe choice (DESIGN-SYSTEM d). */
  initialFocus?: RefObject<HTMLElement | null>;
  /** An alert dialog: a choice the user must make, such as deleting data. */
  alert?: boolean;
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
          role={alert ? "alertdialog" : "dialog"}
          onOpenAutoFocus={
            initialFocus
              ? (event) => {
                  event.preventDefault();
                  initialFocus.current?.focus();
                }
              : undefined
          }
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
