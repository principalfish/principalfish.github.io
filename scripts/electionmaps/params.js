var params = {

  possibleFilterParams : ["incumbent", "region", "majlow", "majhigh", "gains", "byelection"],

  possibleBattlegroundParams : ["b-incumbent", "b-challenger", "b-region", "b-majlow", "b-majhigh"],

  checkParams : function(){

    var filterParams = params.filters();

    if (!(filterParams)){
      params.battleground();
    }

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
          if (input == "yes"){
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
          battleground.handle("battlegrounds-" + param.substring(2), input);
        }
      });
    }
  }
}
