var params = {

  possibleFilterParams : ["incumbent", "region", "majlow", "majhigh", "gains", "byelection"],

  possibleBattlegroundParams : ["incumbent", "challenger", "region", "majlow", "majhigh"],

  checkParams : function(){
    // 
    // var urlFilters = getParameterByName("filters", url);
    // if (urlFilters == "yes" || urlFilters == "true"){
    //   params.filters();
    // } else {
    //   var urlBattle = getParameterByName("battlegrounds", url)
    //   if (urlBattle == "yes" || urlBattle == "true"){
    //     params.battleground();
    //   }
    // }

    params.filters()
  },

  filters : function(){
    var parameterInput = false;
    $.each(params.possibleFilterParams, function(i, param){
      var input = getParameterByName(param, url);

      if (input != null){
        input = input.toLowerCase();
        if (param == "incumbent"){
          filters.selectHandle("filter-current", input);
        } else if (param == "region"){
          filters.selectHandle("filter-region", input);
        }  else if (param == "majlow"){
          filters.majority("low", input);
        }  else if (param == "majhigh"){
          filters.majority("high", input);
        }  else if (param == "gains" || param =="byelection"){
          if (input == "yes" || input == "true"){
            filters.byElection("");
          }
        }
        parameterInput = true;
      }
    })

    if (parameterInput == true){
      return true;
    } else {
      return false;
    }

  },

  battleground: function(){
    if (currentMap.battlegrounds == true){
      var parameterInput = false;
      $.each(params.possibleBattlegroundParams, function(i, param){
        var input = getParameterByName(param, url);

        if (input != null){
          parameterInput = true;
          battleground.handle("battlegrounds-" + param, input);
        }
      });
    }
  }
}
