import { t as translateMessage } from "../../../../src/localization";
import { useState } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { invoke } from "@/host/core";
import {
  LOCAL_WORKSPACE,
  useWorkspaceEnvStore,
  type WorkspaceEnv,
} from "@/modules/workspace";

type Props = {
  onSelect: (env: WorkspaceEnv) => void | Promise<void>;
  onOpenLocation: (path: string) => void | Promise<void>;
};
type Location = { label: string; path: string };

export function WorkspaceEnvSelector({ onSelect, onOpenLocation }: Props) {
  const env = useWorkspaceEnvStore((s) => s.env);
  const distros = useWorkspaceEnvStore((s) => s.distros);
  const loading = useWorkspaceEnvStore((s) => s.loading);
  const refreshDistros = useWorkspaceEnvStore((s) => s.refreshDistros);
  const [locations, setLocations] = useState<Location[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [pathOpen, setPathOpen] = useState(false);
  const [path, setPath] = useState("");
  const [opening, setOpening] = useState(false);
  const refresh = async () => {
    setError("");
    try {
      setLocations(await invoke<Location[]>("workspace_locations"));
    } catch (e) {
      setError(String(e));
    }
    void refreshDistros();
  };
  const navigate = async (value: string) => {
    setOpening(true);
    setError("");
    try {
      await onOpenLocation(value.trim());
      setPathOpen(false);
      setOpen(false);
    } catch (e) {
      setError(String(e));
      if (!pathOpen) setOpen(true);
    } finally {
      setOpening(false);
    }
  };
  return (
    <>
      <DropdownMenu
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (value) void refresh();
        }}
      >
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title={translateMessage("Locations and environments")}
            className="h-6 shrink-0 rounded px-2 text-[11px] text-muted-foreground hover:bg-accent"
          >{translateMessage("Locations · ")}{env.kind === "wsl" ? `WSL: ${env.distro}` : "Local"} ▾
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="max-h-96 min-w-60 overflow-y-auto"
        >
          {locations.map((location) => (
            <DropdownMenuItem
              key={location.path}
              onSelect={() => void navigate(location.path)}
            >
              {location.label}
              <span className="ml-auto max-w-40 truncate pl-3 text-xs text-muted-foreground">
                {location.path}
              </span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuItem
            onSelect={() => {
              setPath("");
              setPathOpen(true);
            }}
          >{translateMessage("Open path…")}</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void onSelect(LOCAL_WORKSPACE)}>{translateMessage("Local environment")}</DropdownMenuItem>
          {distros.map((distro) => (
            <DropdownMenuItem
              key={distro.name}
              onSelect={() =>
                void onSelect({ kind: "wsl", distro: distro.name })
              }
            >{translateMessage("WSL: ")}{distro.name}
            </DropdownMenuItem>
          ))}
          {!distros.length && (
            <DropdownMenuItem disabled>
              {translateMessage(loading
                ? "Loading WSL distributions…"
                : "No WSL distributions available")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void refresh()}>{translateMessage("Refresh locations")}</DropdownMenuItem>
          {error && (
            <p
              role="alert"
              className="max-w-80 px-2 py-1 text-xs text-destructive"
            >
              {error}
            </p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={pathOpen} onOpenChange={setPathOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{translateMessage("Open location")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{translateMessage("Open a folder in a new terminal tab. Use a drive path, network path, or an absolute path in the selected WSL distribution.")}</p>
          <Input
            aria-label={translateMessage("Location path")}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder={translateMessage("C:\\Projects or \\\\server\\share")}
            onKeyDown={(e) => {
              if (e.key === "Enter" && path.trim()) {
                e.preventDefault();
                void navigate(path);
              }
            }}
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button
            disabled={!path.trim() || opening}
            onClick={() => void navigate(path)}
          >
            {translateMessage(opening ? "Opening…" : "Open location")}
          </Button>
        </DialogContent>
      </Dialog>
    </>
  );
}
