import { NewsArticle } from "@/types/news";
import MarketPanel from "@/components/MarketPanel";
import styles from "./page.module.css";
// import { NewsList } from "./components/NewsList"

function formatPublishedAt(value: string | null) {
  if (!value) return "Čas neznámý";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "Čas neznámý";
  }

  return new Intl.DateTimeFormat("cs-CZ", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Prague",
  }).format(date);
}

export default async function Home() {
  const response = await fetch(
    'http://localhost:8000/api/news?limit=20',
    { cache: 'no-store' }
  )

  if (!response.ok) {
    throw new Error('Failed to fetch news')
  }

  const data: NewsArticle[] = await response.json();

  return (
    <main className={styles.dashboard}>
      <section className={styles.market}>
        <MarketPanel />
      </section>

      <aside className={styles.news}>
        <h1>BTC/USD – přehled trhu</h1>
        <h2>Aktuální zprávy</h2>

        {/* {data.map((article) => (
          <article className={styles.newsArticle} key={article.url}>
            <h3>
              <a href={article.url} target="_blank" rel="noreferrer">
                {article.title}
              </a>
            </h3>
            <p className={styles.source}>{article.source}</p>
            {article.summary && <p>{article.summary}</p>}
          </article>
        ))} */}

        {data.map((article) => (
          <article className={styles.newsArticle} key={article.url}>
            <h3>
              <a href={article.url} target="_blank" rel="noreferrer">
                {article.title}
              </a>
            </h3>

            <p className={styles.source}>{article.source}</p>

            <p className={styles.publishedAt}>
              {formatPublishedAt(article.publishedAt)}
            </p>

            {article.summary && <p>{article.summary}</p>}
          </article>
        ))}
      </aside>
    </main>
  );
}
