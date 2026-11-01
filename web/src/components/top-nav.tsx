"use client";

import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "合同审查" },
  { href: "/ask/", label: "法规问答" },
  { href: "/eval", label: "评测看板" },
  { href: "/cost", label: "成本与延迟" },
];

function ThemeToggle() {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("cra-theme", next ? "dark" : "light");
    } catch {}
    setDark(next);
  };
  return (
    <button
      onClick={toggle}
      aria-label={dark ? "切换到浅色" : "切换到深色"}
      className="text-muted-foreground hover:text-foreground cursor-pointer rounded-md p-2 transition-colors"
    >
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

export function TopNav() {
  const path = usePathname();
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b px-4">
      <Link href="/" className="font-semibold tracking-tight">
        Contract Risk Review Agent
        <span className="text-muted-foreground ml-2 hidden text-xs font-normal sm:inline">合同风险审查智能体</span>
      </Link>
      <nav className="inline-flex rounded-lg border p-0.5 text-sm">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={cn(
              "rounded-md px-3 py-1 transition-colors",
              path === l.href || path === l.href.replace(/\/$/, "") ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {l.label}
          </Link>
        ))}
      </nav>
      <div className="ml-auto">
        <ThemeToggle />
      </div>
    </header>
  );
}
