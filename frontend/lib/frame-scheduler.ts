// Serialize expensive mount work across animation frames. Constructing a
// lightweight-charts instance (createChart + panes/studies) is heavy; a
// workspace page with 6 charts that all build synchronously on a page switch
// blocks the main thread for seconds. Running one build per frame lets the
// browser paint between them, so the switch feels instant and the charts fill
// in progressively instead of freezing.
type Task = () => void;

const queue: Task[] = [];
let scheduled = false;

function flush(): void {
  scheduled = false;
  const task = queue.shift();
  if (task) task();
  if (queue.length > 0) {
    scheduled = true;
    requestAnimationFrame(flush);
  }
}

/**
 * Enqueue work to run on a future animation frame, one task per frame.
 * Returns a cancel function (call it if the caller unmounts before its turn).
 */
export function scheduleFrameWork(task: Task): () => void {
  queue.push(task);
  if (!scheduled) {
    scheduled = true;
    requestAnimationFrame(flush);
  }
  return () => {
    const i = queue.indexOf(task);
    if (i >= 0) queue.splice(i, 1);
  };
}
