"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { listProjects, stageLabel, type Project } from "@/lib/api";
import { TikTokConnect } from "@/components/tiktok-connect";
import { YouTubeConnect } from "@/components/youtube-connect";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [listReady, setListReady] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancel = false;
    const load = () => {
      listProjects()
        .then((rows) => {
          if (cancel) return;
          setProjects(rows);
          setListError(null);
          setListReady(true);
        })
        .catch((err: unknown) => {
          if (cancel) return;
          setListError(err instanceof Error ? err.message : "Failed to load projects");
          setListReady(true);
        });
    };
    load();
    const id = setInterval(load, 3000);
    return () => {
      cancel = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    setHydrated(true);
  }, []);

  const search = params.toString();

  useEffect(() => {
    setOpen(false);
  }, [pathname, search]);

  useEffect(() => {
    if (!open) return;
    navRef.current?.querySelector("a")?.focus();
  }, [open]);

  const view = hydrated ? params.get("view") : null;
  const emptyHome = hydrated && listReady && projects.length === 0 && !listError;
  const onNew = hydrated && pathname === "/" && (view === "new" || (view !== "projects" && emptyHome));
  const onProjects = hydrated && pathname === "/" && !onNew;
  const onLive = hydrated && pathname.startsWith("/live");
  const recent = [...projects].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5);

  function closeMenu() {
    setOpen(false);
    menuRef.current?.focus();
  }

  return (
    <div className="shell">
      <button
        ref={menuRef}
        type="button"
        className="menu-btn secondary"
        aria-expanded={open}
        onClick={() => (open ? closeMenu() : setOpen(true))}
      >
        {open ? "Close menu" : "Open menu"}
      </button>
      <aside className={open ? "sidebar open" : "sidebar"}>
        <Link href="/" className="brand">
          <span className="brand-name">Cliping</span>
          <span className="brand-local">Local</span>
        </Link>
        <nav ref={navRef} aria-label="Workspace" className="nav">
          <Link className={onNew ? "nav-link active" : "nav-link"} href="/?view=new">
            New cut
          </Link>
          <Link className={onProjects ? "nav-link active" : "nav-link"} href="/?view=projects">
            Projects
          </Link>
          <Link className={onLive ? "nav-link active" : "nav-link"} href="/live">
            Live
          </Link>
        </nav>
        <div className="side-connect">
          <YouTubeConnect />
          <TikTokConnect />
        </div>
        {recent.length > 0 && (
          <div className="recent">
            <p className="recent-label">Recent</p>
            {recent.map((project) => {
              const href = `/projects/${project.id}`;
              const failed = project.latest_job?.status === "failed" || Boolean(project.latest_job?.error);
              const running =
                project.status === "processing" ||
                project.status === "ingesting" ||
                project.status === "created" ||
                project.status === "exporting" ||
                project.status === "live";
              const ready = project.status === "ready_for_review" || project.status === "done";
              const pip = failed ? "cut" : running ? "hold" : ready ? "take" : "idle";
              return (
                <Link key={project.id} href={href} className={pathname === href ? "recent-row active" : "recent-row"}>
                  <span className={`pip ${pip}`} aria-hidden="true" />
                  <span className="recent-copy">
                    <span className="recent-title">{project.title || project.id.slice(0, 8)}</span>
                    <span className="recent-stage">
                      {project.latest_job ? stageLabel(project.latest_job.stage) : project.status}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        )}
        {listError && <p className="side-error">{listError}</p>}
      </aside>
      {open && <button type="button" className="drawer-backdrop" aria-label="Close menu" onClick={closeMenu} />}
      <div className="canvas">{children}</div>
    </div>
  );
}
