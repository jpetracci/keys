import React from "react";
import "../styles/Transaction.css";

function Transaction({ transaction, onDelete }) {
  const formattedDate = new Date(transaction.trans_date).toLocaleDateString(
    "en-US"
  );

  return (
    <div className="transaction-container">
      <p className="transaction-desciption">{transaction.trans_description}</p>
      <p className="transaction-category">{transaction.trans_category}</p>
      <p className="transaction-date">{formattedDate}</p>
      <button
        className="delete-button"
        onClick={() => onDelete(transaction.id)}
      >
        Delete
      </button>
    </div>
  );
}

export default Note;
