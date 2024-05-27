import { useState, useEffect } from "react";
import api from "../api";
import Transaction from "../components/Transaction";
import "../styles/Home.css";

function Home() {
  const [transactions, setTransactions] = useState([]);
  const [trans_description, setDescription] = useState("");
  const [trans_category, setCategory] = useState("");
  const [trans_amount, setAmount] = useState("");

  useEffect(() => {
    getTransactions();
  }, []);

  const getTransactions = () => {
    api
      .get("/api/transactions/")
      .then((res) => res.data)
      .then((data) => {
        setTransactions(data);
        console.log(data);
      })
      .catch((err) => alert(err));
  };

  const deleteTransaction = (id) => {
    api
      .delete(`/api/transactions/delete/${id}/`)
      .then((res) => {
        if (res.status === 204) alert("transaction was delete");
        else alert("failed to delete");
        getTransactions();
      })
      .catch((error) => alert(error));
  };

  const createTransaction = (e) => {
    e.preventDefault();
    api
      .post("/api/transactions/", { trans_description, trans_category })
      .then((res) => {
        if (res.status === 201) alert("transaction was created");
        else alert("failed to create");
        getTransactions();
      })
      .catch((error) => alert(error));
  };
  return (
    <div>
      <div>
        <h2>Transactions</h2>
        {transactions.map((transaction) => (
          <Transaction
            transaction={transaction}
            onDelete={deleteTransaction}
            key={transaction.id}
          />
        ))}
      </div>
      <h2>Create a transaction</h2>
      <form onSubmit={createTransaction}>
        <label htmlFor="trans_description">Description:</label>
        <input
          type="text"
          id="trans_description"
          name="trans_description"
          required
          onChange={(e) => setDescription(e.target.value)}
          value={trans_description}
        />
        <br />
        <label htmlFor="trans_category">Category:</label>
        <input
          type="text"
          id="trans_category"
          name="trans_category"
          required
          onChange={(e) => setCategory(e.target.value)}
          value={trans_category}
        />
        <br />
        <input type="submit" value="Submit" />
        <br />
      </form>
    </div>
  );
}

export default Home;
