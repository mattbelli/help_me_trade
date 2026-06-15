import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Props = {
  title: string;
  description: string;
  bullets: string[];
};

export function ComingSoonPage({ title, description, bullets }: Props) {
  return (
    <div className="space-y-8">
      <PageHeader title={title} subtitle={description} />
      <Card className="max-w-xl border-border/80">
        <CardHeader>
          <CardTitle className="text-base font-medium tracking-tight">
            On the roadmap
          </CardTitle>
          <CardDescription>
            Navigation is wired — content ships in a later bullet.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ul className="space-y-2 text-sm text-muted-foreground">
            {bullets.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="text-accent">—</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
          <Link
            href="/"
            className="inline-flex h-7 items-center rounded-lg border border-border bg-background px-2.5 text-[0.8rem] font-medium hover:bg-muted"
          >
            Back to overview
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
