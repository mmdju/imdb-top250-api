// Pure list-sort comparator shared by the chart endpoints.
// Nulls always sort last, in both directions: negating the comparison for
// desc must not resurrect them (that was a real bug - unrated seed items
// floated to the top on ?sort=rating&order=desc).
export function compareItems(a, b, sort, order) {
  if (sort === "title") {
    const cmp = String(a.title || "").localeCompare(String(b.title || ""));
    return order === "desc" ? -cmp : cmp;
  }
  const av = a[sort] ?? null;
  const bv = b[sort] ?? null;
  if (av == null && bv == null) return 0;
  if (av == null) return 1; // nulls last, both directions
  if (bv == null) return -1;
  const cmp = av - bv;
  return order === "desc" ? -cmp : cmp;
}
