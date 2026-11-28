import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull, getSettings } from "@/lib/api";
import { dateLabel } from "@/lib/format";
import { Grid, PostCard } from "@/components/ui";
import { jsonLdScript } from "@/lib/json-ld";
import { MarkdownBody } from "@/lib/markdown";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.posts ?? []).map((p: Dict) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/blog/${slug}`, 300);
  if (!data) return { title: "Article not found" };
  const p = data.post;
  return {
    title: p.seo?.title ?? p.title,
    description: p.seo?.description ?? p.excerpt,
    alternates: { canonical: `/blog/${p.slug}` },
    openGraph: { type: "article", title: p.title, description: p.excerpt, publishedTime: p.published_at, authors: [p.author] },
  };
}

/**
 * The post body is rendered from a parsed tree by `@/lib/markdown`, not from an HTML string.
 *
 * There is deliberately no renderer function here and no `dangerouslySetInnerHTML` for `p.body`:
 * the body is staff-authored Markdown, and the only reason a staff-authored field was ever safe to
 * put through innerHTML is that nobody had tried to break it (F-1). `MarkdownBody` cannot emit an
 * element or an attribute that React did not build, so a `javascript:` destination or an
 * attribute-breakout payload arrives as text.
 */
export default async function PostPage({ params }: Props) {
  const { slug } = await params;
  const [data, settings] = await Promise.all([apiOrNull<Dict>(`/blog/${slug}`, 300), getSettings()]);
  if (!data) notFound();
  const p = data.post;

  const ld = {
    "@context": "https://schema.org", "@type": "BlogPosting",
    headline: p.title, description: p.excerpt, datePublished: p.published_at,
    author: { "@type": "Organization", name: p.author ?? settings.brand_name },
    publisher: { "@type": "Organization", name: settings.brand_name },
    mainEntityOfPage: `${settings.site_url}/blog/${p.slug}`,
    keywords: (p.tags ?? []).join(", "),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(ld) }} />
      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/blog" className="hover:text-white">Blog</Link> / <span className="text-white">{p.category}</span>
          </nav>
          <h1 className="mt-4 text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{p.title}</h1>
          <p className="mt-3 text-sm text-brand-100">
            {p.author} · {dateLabel(p.published_at)} · {p.reading_minutes} min read
          </p>
        </div>
      </header>

      <article className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <p className="text-lg leading-relaxed text-slate-700">{p.excerpt}</p>
        <MarkdownBody source={p.body ?? ""} />

        {(p.tags ?? []).length > 0 && (
          <div className="mt-8 flex flex-wrap gap-1.5">
            {p.tags.map((t: string) => (
              <Link key={t} href={`/blog?tag=${encodeURIComponent(t)}`} className="chip bg-slate-100 text-slate-700 hover:bg-slate-200">#{t}</Link>
            ))}
          </div>
        )}

        <div className="card mt-8 bg-brand-950 p-6 text-white">
          <h2 className="text-lg font-bold">Turn this into a plan</h2>
          <p className="mt-2 text-sm leading-relaxed text-brand-100">
            Run the free profile assessment and see which programmes actually match your CGPA, English score and budget.
          </p>
          <Link href="/profile-assessment" className="mt-4 inline-block rounded-xl bg-accent-500 px-5 py-2.5 text-sm font-bold text-brand-950">
            Start the assessment
          </Link>
        </div>

        {(data.related ?? []).length > 0 && (
          <section className="mt-12">
            <h2 className="mb-4 text-xl font-bold text-slate-900">Keep reading</h2>
            <Grid cols={3}>{data.related.map((x: Dict) => <PostCard key={x.id} p={x} />)}</Grid>
          </section>
        )}
      </article>
    </>
  );
}
