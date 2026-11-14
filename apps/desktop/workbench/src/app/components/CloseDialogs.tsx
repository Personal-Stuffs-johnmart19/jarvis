import { t as translateMessage } from "../../../../src/localization";
import type { CloseManyPending } from "@/app/hooks/tabCloseGuards";
import {
  type AppCloseBlocker,
  canOptOutOfAppClosePrompt,
} from "@/app/hooks/useAppCloseGuard";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { setConfirmCloseRunningTerminal } from "@/modules/settings/store";
import type { Tab } from "@/modules/tabs";
import { useId, useState } from "react";

type Props = {
  tabs: Tab[];
  pendingCloseTab: number | null;
  onCancelClose: () => void;
  onConfirmClose: () => void;
  pendingTerminalCloseTab: number | null;
  onCancelTerminalClose: () => void;
  onConfirmTerminalClose: () => void;
  pendingDeleteTabs: number[] | null;
  onCancelDeleteClose: () => void;
  onConfirmDeleteClose: () => void;
  pendingCloseMany: CloseManyPending | null;
  closeManyConfirming: boolean;
  onCancelCloseMany: () => void;
  onConfirmCloseMany: () => void;
  pendingAppClose: AppCloseBlocker | null;
  onCancelAppClose: () => void;
  onConfirmAppClose: () => void;
};

function appCloseMessage(blocker: AppCloseBlocker): string {
  if (blocker.busyAgent) return translateMessage("An agent task is still running. Quitting will stop it and discard any unsaved editor changes.");
  if (blocker.dirtyEditors > 0 && blocker.busyTerminal)
    return translateMessage("A terminal process is still running and {0} files have unsaved changes. Quitting will stop the process and discard the changes.", blocker.dirtyEditors);
  if (blocker.dirtyEditors > 0)
    return translateMessage("Unsaved files: {0}. Quitting will discard these changes.", blocker.dirtyEditors);
  return translateMessage("A process is still running in a terminal. Quitting will terminate it.");
}
function OptOutRow({
  checked,
  onCheckedChange,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="-mt-3 flex items-center justify-center gap-2 sm:justify-start">
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(value) => onCheckedChange(value === true)}
      />
      <Label
        htmlFor={id}
        className="font-normal text-[12px] text-muted-foreground"
      >{translateMessage("Don't ask again about running processes")}</Label>
    </div>
  );
}

async function persistOptOut(): Promise<void> {
  try {
    await setConfirmCloseRunningTerminal(false);
  } catch (e) {
    console.error("close-confirmation opt-out failed", e);
  }
}

function closeManyMessage(pending: CloseManyPending, tabs: Tab[]): string {
  const { kind, dirtyIds, busyLeafIds } = pending;
  const dirtyCount = dirtyIds.length;
  const busyCount = busyLeafIds.length;
  if (dirtyCount === 1 && busyCount === 0) {
  const dirty = tabs.find(
      (tab) => tab.kind === "editor" && dirtyIds.includes(tab.id),
    );
    return dirty?.title
      ? `"${dirty.title}" has unsaved changes. Close it anyway?`
      : "1 tab has unsaved changes. Close it anyway?";
  }
  if (dirtyCount > 0 && busyCount > 0) {
  const dirty = `${dirtyCount} tab${dirtyCount === 1 ? " has" : "s have"} unsaved changes`;
    const busy =
      busyCount === 1
        ? "a process is running"
        : `${busyCount} processes are running`;
    return `${dirty} and ${busy}. Closing will discard the changes and terminate the ${busyCount === 1 ? "process" : "processes"}. Close anyway?`;
  }
  if (dirtyCount > 0) {
    return `${dirtyCount} tabs have unsaved changes. Closing will discard them. Close anyway?`;
  }
  const process =
    busyCount === 1 ? "A process is" : `${busyCount} processes are`;
  return kind === "right"
    ? `${process} running in ${busyCount === 1 ? "a tab" : "tabs"} to the right. Closing will terminate ${busyCount === 1 ? "it" : "them"}. Close anyway?`
    : `${process} running in ${busyCount === 1 ? "another tab" : "other tabs"}. Closing will terminate ${busyCount === 1 ? "it" : "them"}. Close anyway?`;
}

/** Confirmation dialogs for closing dirty editors and terminals with live processes. */
export function CloseDialogs({
  tabs,
  pendingCloseTab,
  onCancelClose,
  onConfirmClose,
  pendingTerminalCloseTab,
  onCancelTerminalClose,
  onConfirmTerminalClose,
  pendingDeleteTabs,
  onCancelDeleteClose,
  onConfirmDeleteClose,
  pendingCloseMany,
  closeManyConfirming,
  onCancelCloseMany,
  onConfirmCloseMany,
  pendingAppClose,
  onCancelAppClose,
  onConfirmAppClose,
}: Props) {
  const [optOutTerminalClose, setOptOutTerminalClose] = useState(false);
  const [optOutAppClose, setOptOutAppClose] = useState(false);
  const appCloseCanOptOut =
    pendingAppClose !== null && canOptOutOfAppClosePrompt(pendingAppClose);

  const confirmTerminalClose = () => {
    if (optOutTerminalClose) void persistOptOut();
    setOptOutTerminalClose(false);
    onConfirmTerminalClose();
  };

  const cancelTerminalClose = () => {
    setOptOutTerminalClose(false);
    onCancelTerminalClose();
  };

  // The pref write has to land before the window closes, or quitting drops it.
  const confirmAppClose = async () => {
    const optOut = appCloseCanOptOut && optOutAppClose;
    setOptOutAppClose(false);
    if (optOut) await persistOptOut();
    onConfirmAppClose();
  };

  const cancelAppClose = () => {
    setOptOutAppClose(false);
    onCancelAppClose();
  };

  return (
    <>
      <AlertDialog
        open={pendingCloseTab !== null}
        onOpenChange={(open) => !open && onCancelClose()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{translateMessage("Unsaved Changes")}</AlertDialogTitle>
            <AlertDialogDescription>
              {tabs.find((t) => t.id === pendingCloseTab)?.title
                ? `"${
                    tabs.find((t) => t.id === pendingCloseTab)?.title
                  }" has unsaved changes. Close anyway?`
                : "This file has unsaved changes. Close anyway?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={onCancelClose}>{translateMessage("Cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmClose}>{translateMessage("Close Anyway")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={pendingTerminalCloseTab !== null}
        onOpenChange={(open) => !open && cancelTerminalClose()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{translateMessage("Close Terminal?")}</AlertDialogTitle>
            <AlertDialogDescription>{translateMessage("A process is running. Closing this tab will terminate it.")}</AlertDialogDescription>
          </AlertDialogHeader>
          <OptOutRow
            checked={optOutTerminalClose}
            onCheckedChange={setOptOutTerminalClose}
          />
          <AlertDialogFooter>
            <AlertDialogCancel onClick={cancelTerminalClose}>{translateMessage("Cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={confirmTerminalClose}>{translateMessage("Close Anyway")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={pendingDeleteTabs !== null}
        onOpenChange={(open) => !open && onCancelDeleteClose()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{translateMessage("Unsaved Changes")}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDeleteTabs?.length === 1
                ? (() => {
                    const title = tabs.find(
                      (t) => t.id === pendingDeleteTabs[0],
                    )?.title;
                    return title
                      ? `"${title}" has unsaved changes. The file has been deleted. Close anyway?`
                      : "This file has unsaved changes. The file has been deleted. Close anyway?";
                  })()
                : `${pendingDeleteTabs?.length ?? 0} files have unsaved changes. They have been deleted. Close all anyway?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={onCancelDeleteClose}>{translateMessage("Cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDeleteClose}>{translateMessage("Close Anyway")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={pendingCloseMany !== null}
        onOpenChange={(open) => !open && onCancelCloseMany()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {translateMessage(pendingCloseMany?.kind === "right"
                ? "Close Tabs to the Right"
                : "Close Other Tabs")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingCloseMany ? closeManyMessage(pendingCloseMany, tabs) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={onCancelCloseMany}>{translateMessage("Cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={closeManyConfirming}
              onClick={(event) => {
                event.preventDefault();
                onConfirmCloseMany();
              }}
            >
              {translateMessage(closeManyConfirming ? "Checking..." : "Close Anyway")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={pendingAppClose !== null}
        onOpenChange={(open) => !open && cancelAppClose()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{translateMessage("Quit Jarvis?")}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingAppClose ? appCloseMessage(pendingAppClose) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {appCloseCanOptOut ? (
            <OptOutRow
              checked={optOutAppClose}
              onCheckedChange={setOptOutAppClose}
            />
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel onClick={cancelAppClose}>{translateMessage("Cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={event => { event.preventDefault(); void confirmAppClose(); }}>{translateMessage("Quit Anyway")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
