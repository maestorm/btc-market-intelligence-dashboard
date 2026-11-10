import styles from "./NewsList.module.css"

const dateFormatter = new Intl.DateTimeFormat("cs-CZ", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "Europe/Prague",
});

export function NewsList({ news }) {
  return (
    <div className={styles.newsRow}>
      {news.map((item) => {
        const date = item.publishedAt
          ? new Date(item.publishedAt)
          : null;

        return (
          <a
            className="news-row"
            href={item.url}
            key={item.url}
            target="_blank"
            rel="noreferrer"
          >
            <time className="news-time">
              {date && !Number.isNaN(date.getTime())
                ? dateFormatter.format(date)
                : "Čas neznámý"}
            </time>

            <span className="news-title">{item.title}</span>
          </a>
        );
      })}
    </div>
  );
}
