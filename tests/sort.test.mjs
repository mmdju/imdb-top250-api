import test from "node:test";
import assert from "node:assert/strict";
import { compareItems } from "../src/sort.mjs";

const item = (rating) => ({ title: "Film", rating });
const sortWith = (arr, sort, order) => [...arr].sort((a, b) => compareItems(a, b, sort, order));

test("nulls last ascending", () => {
  const out = sortWith([item(null), item(7.5), item(9.2)], "rating", "asc");
  assert.deepEqual(out.map((e) => e.rating), [7.5, 9.2, null]);
});

test("nulls last descending (regression: unrated items floated first)", () => {
  const out = sortWith([item(null), item(7.5), item(9.2)], "rating", "desc");
  assert.deepEqual(out.map((e) => e.rating), [9.2, 7.5, null]);
});

test("numbers order both directions", () => {
  assert.deepEqual(
    sortWith([item(8), item(9), item(7)], "rating", "asc").map((e) => e.rating),
    [7, 8, 9]
  );
  assert.deepEqual(
    sortWith([item(8), item(9), item(7)], "rating", "desc").map((e) => e.rating),
    [9, 8, 7]
  );
});

test("title sorts both directions", () => {
  const arr = [{ title: "b" }, { title: "a" }, { title: "c" }];
  assert.deepEqual(sortWith(arr, "title", "asc").map((e) => e.title), ["a", "b", "c"]);
  assert.deepEqual(sortWith(arr, "title", "desc").map((e) => e.title), ["c", "b", "a"]);
});

test("both null compare equal", () => {
  assert.equal(compareItems(item(null), item(null), "rating", "desc"), 0);
});
