#pragma once

#include <map>
#include <optional>
#include <string>
#include <vector>

namespace backtest {

struct Date {
    int year{};
    int month{};
    int day{};

    static Date parse(const std::string& value);
    std::string str() const;
    int serial() const;

    friend bool operator<(const Date& lhs, const Date& rhs);
    friend bool operator==(const Date& lhs, const Date& rhs);
};

struct PriceTable {
    std::vector<Date> dates;
    std::map<std::string, std::vector<double>> adjusted_close;

    static PriceTable load_csv(const std::string& path, const std::string& benchmark);
    const std::vector<double>& series(const std::string& ticker) const;
};

struct Config {
    std::string benchmark{"SPY"};
    double transaction_cost_bps{10.0};
    double initial_capital{100000.0};
    std::optional<Date> start_date;
    std::optional<Date> end_date;
};

struct TargetWeight {
    std::string signal_at;
    Date execution_date;
    std::string security_id;
    double target_weight{};
    std::string provenance_id;
};

struct Trade {
    Date date;
    std::string ticker;
    std::string action;
    double units{};
    double price{};
    double notional{};
    double transaction_cost{};
};

struct EquityPoint {
    Date date;
    double portfolio_value{};
    double daily_return{};
    double benchmark_value{};
    double drawdown{};
    double cash_weight{};
};

struct AccountPoint {
    Date date;
    double pre_trade_value{};
    double cash{};
    double invested_value{};
    double traded_notional{};
    double transaction_cost{};
    double portfolio_value{};
};

struct HoldingPoint {
    Date date;
    std::string ticker;
    double units{};
    double price{};
    double market_value{};
    double weight{};
};

struct Performance {
    double ending_value{};
    double total_return{};
    double cagr{};
    double annualized_volatility{};
    double sharpe_ratio{};
    double maximum_drawdown{};
};

struct Result {
    Config config;
    Date start_date;
    Date end_date;
    std::vector<TargetWeight> executed_targets;
    std::vector<Trade> trades;
    std::vector<EquityPoint> equity_curve;
    std::vector<AccountPoint> account_ledger;
    std::vector<HoldingPoint> holdings;
    Performance portfolio;
    Performance benchmark;
    double total_turnover{};
    double total_transaction_costs{};
    int rebalance_count{};
    int initial_waiting_sessions{};
    double average_cash_weight{};
};

std::vector<TargetWeight> load_target_schedule_csv(const std::string& path);
Result run_target_schedule(
    const PriceTable& prices,
    const std::vector<TargetWeight>& targets,
    const Config& config);

void write_outputs(const Result& result, const std::string& output_directory);

}  // namespace backtest
