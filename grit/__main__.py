import argparse
import logging
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="GRIT: Git Repository Inspector for Teachers"
    )
    parser.add_argument("repo", nargs="?", help="Caminho local ou URL do repositório Git")
    parser.add_argument("--roster", "-r", help="Arquivo JSON de correspondência de alunos")
    parser.add_argument("--cli", action="store_true", help="Força a execução no modo terminal (CLI)")
    parser.add_argument(
        "--no-blame", action="store_true",
        help="Não calcula a autoria sobrevivente (git blame), que é lenta em repositórios grandes",
    )
    parser.add_argument(
        "--verbose", "-v", action="count", default=0,
        help="Mostra o log no terminal (-v: informações, -vv: detalhes)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG if args.verbose > 1 else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )

    # Se a flag --cli não for passada e não houver argumentos obrigatórios no terminal, abre a GUI
    if not args.cli:
        try:
            from grit.gui import GritApp

            app = GritApp()
            if args.repo:
                app.repo_entry.insert(0, args.repo)
            if args.roster:
                app.roster_entry.insert(0, args.roster)
            app.mainloop()
            return
        except ImportError as e:
            print(f"Aviso: Não foi possível carregar a interface gráfica ({e}).")
            print("Executando em modo CLI...")

    # Fallback para execução CLI direta
    from grit.core.analyzer import RepositoryAnalyzer
    from grit.core.metrics import active_weeks, blame_description

    if not args.repo:
        parser.error("O argumento 'repo' é obrigatório no modo CLI.")

    roster_path = Path(args.roster) if args.roster else None
    print(f"Inspecionando repositório: {args.repo} ...")
    analyzer = RepositoryAnalyzer(args.repo, roster_path=roster_path, include_blame=not args.no_blame)
    report = analyzer.analyze()
    concentration = report.concentration

    width = 105
    print("\n" + "=" * width)
    print(f"Total de commits: {report.total_commits}")
    print(f"Mensagens vagas detectadas: {report.suspicious_messages_count}")
    print(f"Commits diretos na main: {report.direct_main_commits_count}")
    if concentration:
        print(f"Desigualdade (Gini): commits {concentration.gini_commits:.2f} | linhas {concentration.gini_churn:.2f}")
    print("=" * width)
    print(
        f"{'Aluno':<25} | {'Commits':<8} | {'+Linhas':<9} | {'-Linhas':<9} | {'% Conv':<7} | "
        f"{'% Commits':<9} | {'Vivas':<7} | Obs"
    )
    print("-" * width)
    for a in sorted(report.authors.values(), key=lambda x: x.total_commits, reverse=True):
        share = concentration.commit_share.get(a.canonical_name, 0.0) * 100 if concentration else 0.0
        alive = str(a.surviving_lines) if report.blame_ref else "-"
        print(
            f"{a.canonical_name:<25} | {a.total_commits:<8} | {a.additions:<9} | {a.deletions:<9} | "
            f"{a.conventional_adherence_rate:<6.1f}% | {share:<8.1f}% | {alive:<7} | {a.identity_note}"
        )
    print("=" * width)

    print("Branches:")
    for b in report.branches:
        when = b.last_commit_date.strftime("%Y-%m-%d") if b.last_commit_date else "-"
        print(f"  {b.name:<40} {b.status:<11} último commit: {when}")

    activity = report.activity
    print("\nRitmo semanal (semana iniciada na segunda-feira):")
    if activity and activity.weeks:
        total = len(activity.weeks)
        active = active_weeks(activity)
        print(f"  Equipe: {active}/{total} semanas ativas ({active / total:.1%}).")
        for week in activity.weekly:
            print(
                f"  {week.week:%Y-%m-%d}: {week.commits} commits, "
                f"+{week.additions}/-{week.deletions} linhas, {week.active_authors} autores ativos"
            )
        print("  Regularidade por autor:")
        for name in sorted(activity.counts):
            count = active_weeks(activity, name)
            print(f"    {name}: {count}/{total} semanas ({count / total:.1%})")
    else:
        print("  Nenhuma atividade semanal disponível.")

    print("\nHotspots por arquivo (até 20, ordenados por churn):")
    for hotspot in report.hotspots[:20]:
        print(
            f"  {hotspot.path}: {hotspot.commits} commits, "
            f"+{hotspot.additions}/-{hotspot.deletions} linhas, churn {hotspot.churn}"
        )
    if not report.hotspots:
        print("  Nenhum arquivo elegível encontrado.")
    elif len(report.hotspots) > 20:
        print(f"  ... {len(report.hotspots) - 20} arquivo(s) adicional(is).")

    print("\nAutoria sobrevivente por módulo (. = raiz):")
    print(f"  {blame_description(report)}")
    if report.blame_details:
        for module, authors in sorted(report.blame_details.modules.items()):
            total = sum(authors.values())
            for name, lines in sorted(authors.items(), key=lambda item: (-item[1], item[0])):
                share = f"{lines / total:.1%}" if total else "—"
                print(f"  {module} | {name}: {lines} linhas ({share})")


if __name__ == "__main__":
    main()

