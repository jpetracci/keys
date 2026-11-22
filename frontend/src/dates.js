// Today's calendar date where the user is, as YYYY-MM-DD (the value an
// <input type="date"> expects). Date.toISOString() reports the UTC date,
// which is already tomorrow during the evening in the Americas and still
// yesterday during the morning in Asia and Australia.
export function localDateString(date = new Date()) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}
