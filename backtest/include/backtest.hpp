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

struct Security {
    std::string ticker;
    std::string company;
    std::string sector;
    std::string industry;
    std::string sector_etf;
};

struct PriceTable {
    std::vector<Date> dates;
    std::map<std::string, std::vector<double>> adjusted_close;

    static PriceTable load_csv(const std::string& path, const std::string& benchmark);
    const std::vector<double>& series(const std::string& ticker) const;
};

std::vector<Security> load_universe_csv(const std::string& path);

struct MomentumMetrics {
    double return_1m{};
    double return_3m{};
    double return_6m{};
    double relative_strength_3m{};
    double volatility_3m{};
    double drawdown_3m{};
};

struct Config {
    std::string benchmark{"SPY"};
    int top_sectors{3};
    int top_industries_per_sector{2};
    int top_companies_per_industry{2};
    int rebalance_months{3};
    int trend_days{200};
    double transaction_cost_bps{10.0};
    double initial_capital{100000.0};
    double maximum_position_weight{0.15};
    double maximum_sector_weight{0.40};
    bool inverse_volatility_weights{true};
    std::optional<Date> start_date;
    std::optional<Date> end_date;
};

struct Selection {
    Date signal_date;
    Date execution_date;
    std::string ticker;
    std::string company;
    std::string sector;
    std::string industry;
    double sector_score{};
    double industry_score{};
    double company_score{};
    double volatility_3m{};
    double target_weight{};
};

struct Trade {
    Date date;
    std::string ticker;
    std::string action;
    double shares{};
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

struct Performance {
    double ending_value{};
    double total_return{};
    double cagr{};
    double annualized_volatility{};
    double sharpe_ratio{};
    double sortino_ratio{};
    double maximum_drawdown{};
};

struct Result {
    Config config;
    Date start_date;
    Date end_date;
    std::vector<Selection> selections;
    std::vector<Trade> trades;
    std::vector<EquityPoint> equity_curve;
    Performance portfolio;
    Performance benchmark;
    double total_turnover{};
    double total_transaction_costs{};
    int rebalance_count{};
};

class Engine {
  public:
    Engine(PriceTable prices, std::vector<Security> universe, Config config);
    Result run() const;

  private:
    PriceTable prices_;
    std::vector<Security> universe_;
    Config config_;
    std::map<std::string, std::vector<double>> industry_indexes_;

    std::vector<Selection> select_portfolio(std::size_t signal_index) const;
};

void write_outputs(const Result& result, const std::string& output_directory);

}  // namespace backtest
